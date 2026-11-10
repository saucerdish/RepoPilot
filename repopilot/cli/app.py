"""Interactive terminal shell; one worker owns all lead-model operations."""

import argparse
import asyncio
import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from repopilot.agent.permission import ApprovalMode, session_grant

from . import VERSION
from .ui import COMMANDS, TerminalUI, format_elapsed


def arguments(argv=None):
    parser = argparse.ArgumentParser(description="RepoPilot · Your repo. Your course.")
    parser.add_argument("repository", nargs="?", default=".", help="target Git repository")
    parser.add_argument("--version", action="version", version=f"RepoPilot {VERSION}")
    parser.add_argument("--model", help="model ID for this session (overrides MODEL_ID)")
    parser.add_argument("--base-url", help="OpenAI-compatible API base URL")
    parser.add_argument("--env-file", type=Path, help="explicit dotenv configuration file")
    parser.add_argument("--mcp-config", type=Path, help="host-approved MCP configuration")
    parser.add_argument("--task", help="run one task, then exit; manual mode denies unapproved operations")
    parser.add_argument("--mode", choices=("manual", "auto"), help="approval mode (default: manual)")
    parser.add_argument("--allow-command", action="append", default=[], help="preauthorize one exact shell command")
    parser.add_argument("--plain", action="store_true", help="disable colors and animated status")
    parser.add_argument("--theme", choices=("auto", "light", "dark"), help="terminal appearance (default: auto)")
    parser.add_argument("--verbose", action="store_true", help="show longer tool output")
    parser.add_argument("--demo", action="store_true", help="preview the interface offline; no API calls")
    parser.add_argument("--export-svg", type=Path, help="save the offline demo as an SVG")
    args = parser.parse_args(argv)
    if args.export_svg and not args.demo:
        parser.error("--export-svg requires --demo")
    if args.demo and args.task:
        parser.error("--demo and --task cannot be combined")
    if args.env_file and not args.env_file.is_file():
        parser.error(f"configuration file does not exist: {args.env_file}")
    if not args.demo and not (Path(args.repository).resolve() / ".git").exists():
        parser.error(f"not a Git repository: {Path(args.repository).resolve()}")
    return args


def git(workspace, *args):
    result = subprocess.run(["git", *args], cwd=workspace, capture_output=True,
                            text=True, errors="replace", timeout=10)
    if result.returncode:
        raise RuntimeError((result.stderr or "Git command failed").strip())
    return result.stdout[:200_000]


def branch_name(workspace):
    try:
        return git(workspace, "symbolic-ref", "--quiet", "--short", "HEAD").strip()
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        try:
            return git(workspace, "rev-parse", "--short", "HEAD").strip() + " (detached)"
        except (RuntimeError, OSError, subprocess.TimeoutExpired):
            return "no commits"


SHELL_TIMEOUT = 120
PASTE_LINES, PASTE_CHARS = 10, 1500  # fold pastes longer than this
NOTIFY_AFTER = 30  # seconds; shorter tasks finish while you are still watching


def safe_url(value):
    parsed = urlsplit(value or "https://api.openai.com/v1")
    return parsed.scheme + "://" + parsed.netloc.split("@")[-1] + parsed.path


def error_message(exc):
    kind = type(exc).__name__
    if kind in ("AuthenticationError", "PermissionDeniedError"):
        return "API authentication failed. Check your API key, model access and base URL."
    message = str(exc)
    for name in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY"):
        value = os.environ.get(name)
        if value:
            message = message.replace(value, "[redacted]")
    return f"{kind}: {message[:1200]}"


class TerminalClient:
    def __init__(self, args, ui=None, builder=None):
        self.args = args
        self.approval_mode = ApprovalMode(getattr(args, "mode", None) or "manual")
        self.args.mode = self.approval_mode.value
        self.workspace = Path(args.repository).resolve()
        self.ui = ui or TerminalUI(plain=args.plain, verbose=args.verbose, theme=args.theme or "auto")
        self.ui.workspace = self.workspace
        self.builder = builder
        self.host = None
        self.session = None
        self.loop = None
        self.busy = False
        self.terminal = sys.stdin.isatty() and sys.stdout.isatty()
        self.cancellation = threading.Event()
        # Actions the user chose to "always allow" until this process exits.
        self.session_allowed = set()
        # Output of "!" commands, handed to the model with the next message.
        self.shell_notes = []
        # Placeholder -> full text for long pastes in the composer.
        self.pastes = {}
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="repopilot-session")
        self.usage = {"api_calls": 0, "api_errors": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.model = args.model or os.getenv("MODEL_ID", "")
        self.branch = branch_name(self.workspace)
        self.mcp_servers = {}
        if args.mcp_config:
            self.mcp_servers = json.loads(args.mcp_config.read_text(encoding="utf-8"))
            if not isinstance(self.mcp_servers, dict):
                raise ValueError("MCP configuration must be a JSON object")

    def _ensure_host(self):
        if self.host is None:
            if self.builder is None:
                if not os.getenv("OPENAI_API_KEY") or not self.model:
                    raise ValueError("Set OPENAI_API_KEY in your .env and select a model with /model <id> or --model <id>.")
                from repopilot.factory import build_agent
                builder = build_agent
            else:
                builder = self.builder
            from repopilot.runtime.host import AgentHost
            self.host = AgentHost(builder(self.workspace, approval=self._approve,
                                          allow_commands=self.args.allow_command,
                                          mcp_servers=self.mcp_servers, events=self.ui.event,
                                          cancellation=self.cancellation, mode=self.approval_mode))
        return self.host

    def _approve(self, name, args, reason):
        # Never consume queued task text as an approval, including piped stdin.
        if not self.terminal or self.args.task or self.host is None or not self.host.agent.interactive:
            return False
        if session_grant(name, args, reason)[0] in self.session_allowed:
            return True
        future = asyncio.run_coroutine_threadsafe(self._ask_approval(name, args, reason), self.loop)
        try:
            return future.result()
        except (KeyboardInterrupt, EOFError, asyncio.CancelledError):
            return False

    async def _ask_approval(self, name, args, reason):
        from prompt_toolkit import PromptSession
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.styles import DynamicStyle
        from .composer import frame_session
        self.ui.permission(name, args, reason)
        bindings = KeyBindings()

        @bindings.add("c-c")
        def cancel(event):
            self.interrupt()
            event.app.exit(result="")

        # A separate session has no history, completions or toolbar from the task input.
        session = PromptSession(style=DynamicStyle(self._prompt_style), reserve_space_for_menu=0,
                                key_bindings=bindings, erase_when_done=True,
                                color_depth=self._prompt_color_depth(),
                                placeholder=[("class:placeholder", "y once · a always this session · Enter declines")])
        key, scope, _ = session_grant(name, args, reason)
        frame_session(session, [("class:input-hint", " y allows once · a allows " + scope + " for this session")],
                      title=" Permission ")
        try:
            answer = await session.prompt_async("Allow? [y/a/N] ", handle_sigint=False)
        except (KeyboardInterrupt, EOFError):
            return False
        answer = answer.strip().lower()
        if answer in ("a", "always"):
            self.session_allowed.add(key)
            self.ui.notice("Allowed for this session: " + scope)
            return True
        allowed = answer in ("y", "yes")
        self.ui.notice("Allowed once" if allowed else "Declined")
        return allowed

    def _invoke(self, fn, *args):
        from repopilot.llm.telemetry import collect_metrics
        with collect_metrics() as metrics:
            try:
                return fn(*args)
            finally:
                summary = metrics.summary()
                for key in self.usage:
                    self.usage[key] += summary.get(key, 0)

    async def call(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self.worker, self._invoke, fn, *args)

    def interrupt(self):
        if self.busy and not self.cancellation.is_set():
            self.cancellation.set()
            self.ui.notice("Stopping… No further tools will run in this turn.")

    async def task(self, prompt):
        self.cancellation.clear()
        self.busy = True
        self.ui.tool_count = 0
        self.ui.thinking()
        started = time.monotonic()
        before = dict(self.usage)
        try:
            if self.shell_notes:
                prompt = ("Commands I ran in the terminal since my last message:\n\n"
                          + "\n\n".join(self.shell_notes) + "\n\n" + prompt)
                self.shell_notes = []

            def submit():
                result = self._ensure_host().submit(prompt)
                self.branch = branch_name(self.workspace)
                return result
            answer = await self.call(submit)
            if self.cancellation.is_set():
                self.ui.notice("Interrupted. Changes already made have been preserved.")
                return 130
            metrics = {key: value - before[key] for key, value in self.usage.items()}
            self.ui.answer(answer, time.monotonic() - started, metrics, self.host.agent.goal.state)
            goal = self.host.agent.goal.state
            return 1 if goal and goal["status"] in ("impossible", "error", "limited", "active", "deferred") else 0
        except Exception as exc:
            if self.cancellation.is_set():
                self.ui.notice("Interrupted. Changes already made have been preserved.")
                return 130
            self.ui.notice(error_message(exc), error=True)
            return 1
        finally:
            self.busy = False
            self.ui.stop()
            self.cancellation.clear()
            elapsed = time.monotonic() - started
            if self.terminal and elapsed >= NOTIFY_AFTER:
                # Long tasks are when people switch away; tell them it is done.
                self.ui.notify(f"RepoPilot finished in {format_elapsed(elapsed)}")

    def _pending(self):
        return self.host is not None and self.host.agent.goal.pending()

    def _local_command(self, command, value):
        if command == "/status":
            changes = git(self.workspace, "status", "--short", "--untracked-files=normal").strip()
            self.branch = branch_name(self.workspace)
            self.ui.details("Session", {
                "Workspace": self.workspace, "Branch": self.branch,
                "Model": self.model or "not configured",
                "Provider": safe_url(os.getenv("OPENAI_BASE_URL")),
                "Theme": self.ui.mode,
                "API key": "configured" if os.getenv("OPENAI_API_KEY") else "missing",
                "Mode": f"{self.approval_mode.value} · {self.approval_mode.description}",
                "Permissions": ("automatically approve requested operations" if self.approval_mode.value == "auto"
                                else f"{len(self.args.allow_command)} exact commands preauthorized"),
                "Lead usage": f"{self.usage['api_calls']} requests · {self.usage['total_tokens']:,} tokens · {self.usage['api_errors']} API errors",
                "Changes": "\n".join(changes.splitlines()[:16]) or "clean",
            })
        elif command == "/model":
            if value:
                if len(value.split()) != 1:
                    raise ValueError("Use /model <one-model-id>")
                if self._pending():
                    raise ValueError("Wait for background work before switching models.")
                self.model = value
                os.environ["MODEL_ID"] = value
                if self.host:
                    self.host.agent.llm.model = value
            self.ui.notice("Model: " + (self.model or "not configured; use /model <id>"))
        elif command == "/mode":
            if value:
                self.approval_mode.set(value)
                self.args.mode = value
            self.ui.notice(f"Mode: {self.approval_mode.value} · {self.approval_mode.description}.")
        elif command == "/theme":
            if value:
                from .config import save_theme
                from .theme import palette_for
                palette_for(value)
                save_theme(value)
                self.ui.set_theme(value)
                self.args.theme = value
            self.ui.notice("Theme: " + self.ui.mode + ". Use /theme auto, light or dark.")
        elif command == "/diff":
            paths = shlex.split(value) if value else []
            try:
                changes = git(self.workspace, "diff", "--no-ext-diff", "--no-textconv", "HEAD", "--", *paths)
            except RuntimeError:
                changes = git(self.workspace, "diff", "--no-ext-diff", "--no-textconv", "--cached", "--", *paths)
                changes += git(self.workspace, "diff", "--no-ext-diff", "--no-textconv", "--", *paths)
            self.ui.diff(changes)
        elif command == "/tasks":
            from repopilot.runtime.tasks import TaskStore
            if self.host:
                self.ui.details("Plan", {"Current": self.host.agent.registry.get("todo_write").render()})
            tasks = TaskStore(self.workspace).list()
            self.ui.details("Tasks", {t["id"]: f"{t['status']} · {t['subject']}" for t in tasks} or {"Queue": "No persistent tasks"})
        elif command == "/goal":
            from repopilot.runtime.goal import GoalController
            goal = self.host.agent.goal if self.host else GoalController(self.workspace, None)
            self.ui.details("Goal", {"Status": goal.status()})
        elif command == "/clear":
            if self._pending():
                raise ValueError("Wait for background work before clearing its conversation evidence.")
            if self.host:
                self.host.history.clear()
                self.host.active_request = ""
            self.ui.notice("Conversation cleared. Files, memories, tasks and goal state are preserved.")
        elif command == "/compact":
            if not self.host or not self.host.history:
                self.ui.notice("No conversation to compact.")
            else:
                self.host.agent.context.compact(self.host.history, self.host.active_request, keep=5)
                self.ui.notice("Conversation compacted. Original history is archived on disk.")
        elif command == "/export":
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
            path = Path(value).expanduser() if value else self.workspace / ".repopilot" / "exports" / f"session-{stamp}.json"
            if not path.is_absolute():
                path = self.workspace / path
            if path.suffix.lower() != ".json":
                raise ValueError("Export path must end with .json")
            payload = {"model": self.model, "usage": self.usage,
                       "messages": self.host.history if self.host else []}
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
            self.ui.notice(f"Conversation exported to {path}")

    async def handle(self, text):
        text = text.strip()
        if not text:
            return True
        if text in ("q", "exit", "/exit", "/quit"):
            return False
        if text.startswith("!"):
            await self.shell(text[1:].strip())
            return True
        if text.startswith("/"):
            command, _, value = text.partition(" ")
            value = value.strip()
            if command not in COMMANDS:
                self.ui.notice(f"Unknown command: {command}. Use /help to see commands.", error=True)
            elif command == "/help":
                self.ui.help()
            elif command == "/goal" and value:
                await self.task(text)
            else:
                try:
                    await self.call(self._local_command, command, value)
                except Exception as exc:
                    self.ui.notice(error_message(exc), error=True)
            return True
        await self.task(text)
        return True

    async def shell(self, command):
        """Run the user's own "!" command in the repository, like a terminal."""
        if not command:
            self.ui.notice("Type a command after !, e.g. !git status")
            return
        self.ui.thinking("$ " + command[:60])
        started = time.monotonic()
        try:
            result = await asyncio.to_thread(
                subprocess.run, command, shell=True, cwd=self.workspace, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, errors="replace", timeout=SHELL_TIMEOUT)
            output, code = result.stdout + result.stderr, result.returncode
        except subprocess.TimeoutExpired as exc:
            output = ((exc.stdout or b"").decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""))
            output += f"\nStopped after {SHELL_TIMEOUT}s"
            code = "timeout"
        except OSError as exc:
            output, code = str(exc), "error"
        self.ui.terminal(command, output, code, time.monotonic() - started, failed=code != 0)
        self.shell_notes.append(f"$ {command}\n(exit {code})\n{output[-4000:]}".rstrip())

    def expand_pastes(self, text):
        for token, data in self.pastes.items():
            text = text.replace(token, data)
        return text

    def _shell_mode(self):
        return bool(self.session) and self.session.default_buffer.text.startswith("!")

    def _prompt_style(self):
        from prompt_toolkit.styles import Style
        from .theme import palette_for
        palette = palette_for("auto", {}) if self.args.plain else self.ui.palette
        return Style.from_dict(palette.prompt_styles())

    def _prompt_color_depth(self):
        from prompt_toolkit.output import ColorDepth
        if self.args.plain:
            return ColorDepth.DEPTH_1_BIT
        if os.getenv("COLORTERM", "").lower() in ("truecolor", "24bit"):
            return ColorDepth.DEPTH_24_BIT
        return None

    def _make_session(self):
        from prompt_toolkit import PromptSession
        from prompt_toolkit.enums import DEFAULT_BUFFER
        from prompt_toolkit.filters import has_focus
        from prompt_toolkit.keys import Keys
        from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
        from prompt_toolkit.completion import WordCompleter
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.styles import DynamicStyle
        from .composer import frame_session
        history = self.workspace / ".repopilot" / "cli" / "history"
        history.parent.mkdir(parents=True, exist_ok=True)
        bindings = KeyBindings()
        editing = has_focus(DEFAULT_BUFFER)

        @bindings.add("tab", filter=editing)
        def complete(event):
            buffer = event.current_buffer
            prefix = buffer.document.text_before_cursor
            matches = [command for command in COMMANDS if prefix.startswith("/") and command.startswith(prefix)]
            if len(matches) == 1:
                # Complete short command lists synchronously, even when Tab and
                # Enter arrive in the same input batch.
                buffer.cancel_completion()
                buffer.delete_before_cursor(len(buffer.document.text_before_cursor))
                buffer.insert_text(matches[0])
            elif buffer.complete_state:
                buffer.complete_next()
            else:
                buffer.start_completion(select_first=True)

        @bindings.add("enter", filter=editing)
        def send(event):
            buffer = event.current_buffer
            if buffer.complete_state:
                selected = buffer.complete_state.current_completion
                if selected:
                    buffer.apply_completion(selected)
                else:
                    buffer.complete_state = None
            if buffer.text.endswith("\\"):
                buffer.delete_before_cursor()
                buffer.insert_text("\n")
            else:
                buffer.validate_and_handle()

        @bindings.add(Keys.BracketedPaste, filter=editing)
        def paste(event):
            # Long pastes (logs, tracebacks) would fill the composer and the
            # transcript; keep a placeholder and expand it when sending.
            data = event.data.replace("\r\n", "\n").replace("\r", "\n")
            lines = data.count("\n") + 1
            if lines > PASTE_LINES or len(data) > PASTE_CHARS:
                token = f"[Pasted #{len(self.pastes) + 1} · {lines} lines]"
                self.pastes[token] = data
                data = token
            event.current_buffer.insert_text(data)

        @bindings.add("escape", "enter", filter=editing)
        @bindings.add("c-j", filter=editing)
        def newline(event):
            event.current_buffer.insert_text("\n")

        @bindings.add("c-c", filter=editing)
        def cancel(event):
            if self.busy:
                self.interrupt()
            else:
                event.current_buffer.reset()
                # KeyboardInterrupt escaping an asyncio Task stops the entire
                # event loop before its caller can inspect task.result().
                event.app.exit(result=None)

        session = PromptSession(history=FileHistory(str(history)),
                             auto_suggest=AutoSuggestFromHistory(),
                             completer=WordCompleter(list(COMMANDS), sentence=True, meta_dict=COMMANDS),
                             complete_while_typing=True, key_bindings=bindings,
                             multiline=True, style=DynamicStyle(self._prompt_style),
                             reserve_space_for_menu=0, erase_when_done=True,
                             color_depth=self._prompt_color_depth(),
                             placeholder=[("class:placeholder", "Describe a change, ask a question, or paste an error…")],
                             enable_open_in_editor=False)
        from .composer import HideLeadingBang
        # Before the prompt processors, so only the draft's own "!" is hidden.
        session.app.layout.current_window.content.input_processors.insert(0, HideLeadingBang())
        return frame_session(
            session, self._toolbar,
            title=lambda: [("class:frame.label.shell", " Terminal ")] if self._shell_mode() else " Message ",
            border=lambda: "class:frame.border.shell" if self._shell_mode() else "class:frame.border")

    def _toolbar(self):
        if self._shell_mode():
            return [("class:input-hint.shell",
                     f" Enter runs in {self.workspace.name} · output goes to the model with your next message")]
        hint = " Enter send · Alt+Enter new line · ! terminal"
        hint += " · Tab complete · ↑ history" if self.ui.console.width >= 72 else " · /help"
        if self.ui.console.width >= 92:
            hint += f" · {self.usage['total_tokens']:,} tokens"
        return [("class:input-hint", hint)]

    async def _interactive(self):
        from prompt_toolkit.patch_stdout import patch_stdout
        self.session = self._make_session()
        with patch_stdout(raw=True):
            while True:
                prompt = asyncio.create_task(self.session.prompt_async(
                    lambda: [("class:prompt.shell", " $ ")] if self._shell_mode() else [("class:prompt", " ❯ ")],
                    # The CLI owns SIGINT between prompts too. PromptSession's
                    # default handler removes the loop callback when it exits.
                    handle_sigint=False,
                    placeholder=[("class:placeholder", "Describe a change, ask a question, or paste an error…")],
                    prompt_continuation=lambda width, line, soft: " · "))
                try:
                    while not prompt.done():
                        await asyncio.wait({prompt}, timeout=.5)
                        if not prompt.done() and self.host:
                            self.busy = True
                            try:
                                outputs = await self.call(self.host.poll)
                                for output in outputs:
                                    if output:
                                        self.ui.answer(output)
                            except Exception as exc:
                                self.ui.notice(error_message(exc), error=True)
                            finally:
                                self.busy = False
                                self.ui.stop()
                                self.cancellation.clear()
                    text = prompt.result()
                    if text is None:
                        self.ui.notice("Draft cleared. Ctrl+D exits the session.")
                        continue
                except KeyboardInterrupt:
                    self.ui.notice("Draft cleared. Ctrl+D exits the session.")
                    continue
                except EOFError:
                    break
                finally:
                    if not prompt.done():
                        prompt.cancel()
                        await asyncio.gather(prompt, return_exceptions=True)
                # The composer erases itself on Enter; keep a one-line echo.
                # "!" commands are echoed by their Terminal window instead.
                if text.strip() and not text.strip().startswith("!"):
                    self.ui.user(text.strip())
                if not await self.handle(self.expand_pastes(text)):
                    break

    async def run(self):
        self.loop = asyncio.get_running_loop()
        previous_handler = signal.getsignal(signal.SIGINT)
        installed_handler = False
        fallback_handler = False
        try:
            try:
                self.loop.add_signal_handler(signal.SIGINT, self.interrupt)
                installed_handler = True
            except (NotImplementedError, RuntimeError, ValueError):
                try:
                    signal.signal(signal.SIGINT, lambda *_: self.loop.call_soon_threadsafe(self.interrupt))
                    fallback_handler = True
                except ValueError:
                    pass
            if self.args.task:
                return await self.task(self.args.task)
            self.ui.welcome(self.workspace, self.model, self.branch,
                            configured=bool(os.getenv("OPENAI_API_KEY") and self.model),
                            permission_mode=self.approval_mode.value)
            if self.terminal:
                await self._interactive()
            else:
                for line in sys.stdin:
                    self.ui.user(line.rstrip())
                    if not await self.handle(line):
                        break
            self.ui.notice("Session closed. Your changes are saved in the workspace.")
            return 0
        finally:
            self.ui.stop()
            self.cancellation.set()
            if self.host:
                await self.call(self.host.close)
                client = getattr(self.host.agent.llm, "client", None)
                if client:
                    client.close()
            self.worker.shutdown(wait=True)
            if installed_handler:
                self.loop.remove_signal_handler(signal.SIGINT)
            if installed_handler or fallback_handler:
                signal.signal(signal.SIGINT, previous_handler)


def main(argv=None):
    args = arguments(argv)
    args.plain = args.plain or "NO_COLOR" in os.environ or os.getenv("TERM") == "dumb"
    if args.demo:
        from .demo import run_demo
        run_demo(Path(args.repository).resolve(), args.export_svg, args.plain, args.theme or "auto",
                 permission_mode=args.mode or "manual")
        return 0
    try:
        from .config import configure_environment
        configure_environment(args)
        return asyncio.run(TerminalClient(args).run())
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        TerminalUI(plain=args.plain).notice(error_message(exc), error=True)
        return 1
