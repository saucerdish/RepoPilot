import asyncio
import io
import json
import os
import signal
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from prompt_toolkit.output import DummyOutput
from rich.console import Console

from repopilot.agent.loop import Agent
from repopilot.agent.hooks import HookManager
from repopilot.cli.app import TerminalClient, arguments, error_message, safe_url
from repopilot.cli.demo import run_demo
from repopilot.cli.ui import TerminalUI
from repopilot.factory import build_agent
from repopilot.tools.files import WriteFileTool
from repopilot.tools.registry import ToolRegistry
from tests_helpers import ScriptedLLM


def reply(calls=None):
    calls = calls or []
    return SimpleNamespace(content="Done.", tool_calls=calls,
                           model_dump=lambda **_: {"role": "assistant", "content": "Done.",
                                                   "tool_calls": [{"id": c.id, "type": "function",
                                                                   "function": {"name": c.function.name, "arguments": c.function.arguments}}
                                                                  for c in calls]})


def call(identifier, name, args):
    return SimpleNamespace(id=identifier, function=SimpleNamespace(name=name, arguments=json.dumps(args)))


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "sample.py").write_text("original\n")
        subprocess.run(["git", "add", "sample.py"], cwd=self.root, check=True)
        subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                        "commit", "-qm", "fixture"], cwd=self.root, check=True)
        self.output = io.StringIO()
        self.ui = TerminalUI(Console(file=self.output, width=92, color_system=None), animate=False)

    def tearDown(self):
        self.directory.cleanup()

    def client(self, llm=None, extra=()):
        args = arguments([str(self.root), "--plain", "--model", "test-model", *extra])
        return TerminalClient(args, self.ui,
                              builder=lambda *a, **kw: build_agent(*a, llm=llm or ScriptedLLM(), **kw))

    def test_pipe_does_not_consume_next_command_as_approval(self):
        class ShellLLM(ScriptedLLM):
            def __init__(self):
                self.turn = 0

            def chat(self, messages, tools):
                self.turn += 1
                return reply([call("shell", "bash", {"command": "echo SHOULD_NOT_RUN"})]) if self.turn == 1 else reply()

        client = self.client(ShellLLM())
        client.terminal = False
        with patch("sys.stdin", io.StringIO("do work\n/status\n/exit\n")):
            self.assertEqual(asyncio.run(client.run()), 0)
        self.assertIn("Permission denied", self.output.getvalue())
        self.assertIn("Session", self.output.getvalue())
        results = [m["content"] for m in client.host.history if m["role"] == "tool"]
        self.assertEqual(results, ["Permission denied by user"])

    def test_diff_includes_staged_and_unstaged_changes(self):
        client = self.client()
        try:
            (self.root / "sample.py").write_text("staged\n")
            subprocess.run(["git", "add", "sample.py"], cwd=self.root, check=True)
            (self.root / "sample.py").write_text("working\n")
            client._local_command("/diff", "sample.py")
            self.assertIn("-original", self.output.getvalue())
            self.assertIn("+working", self.output.getvalue())
        finally:
            client.worker.shutdown()

    def test_manual_denies_and_auto_runs_noninteractive_shell(self):
        class ShellLLM(ScriptedLLM):
            turn = 0

            def chat(self, messages, tools):
                self.turn += 1
                return reply([call("shell", "bash", {"command": "echo MODE_ALLOWED"})]) if self.turn == 1 else reply()

        for mode in ("manual", "auto"):
            with self.subTest(mode=mode):
                client = self.client(ShellLLM(), extra=("--mode", mode, "--task", "run command"))
                client.terminal = False
                with patch.object(client, "_ask_approval", side_effect=AssertionError("Unexpected prompt")):
                    self.assertEqual(asyncio.run(client.run()), 0)
                result = next(m["content"] for m in client.host.history if m["role"] == "tool")
                if mode == "manual":
                    self.assertEqual(result, "Permission denied by user")
                else:
                    self.assertIn("Exit code: 0", result)
                    self.assertIn("MODE_ALLOWED", result)

    def test_mode_switch_preserves_host_and_conversation(self):
        client = self.client()

        async def scenario():
            try:
                await client.task("inspect")
                host = client.host
                history = list(host.history)
                await client.handle("/mode auto")
                self.assertEqual(client.approval_mode.value, "auto")
                self.assertIsNone(host.agent.hooks.trigger("PreToolUse", "bash", {"command": "echo fixture"}))
                await client.handle("/status")
                self.assertIn("auto · no approval prompts", self.output.getvalue())
                await client.handle("/mode manual")
                self.assertIn("Permission denied", host.agent.hooks.trigger("PreToolUse", "bash", {"command": "echo fixture"}))
                await client.handle("/mode invalid")
                self.assertEqual(client.approval_mode.value, "manual")
                self.assertIn("Mode must be manual or auto", self.output.getvalue())
                self.assertIs(client.host, host)
                self.assertEqual(host.history, history)
            finally:
                if client.host:
                    await client.call(client.host.close)

        try:
            asyncio.run(scenario())
        finally:
            client.worker.shutdown()

    def test_model_switch_preserves_conversation_and_exports(self):
        client = self.client()

        async def scenario():
            await client.task("inspect")
            with patch.dict(os.environ):
                await client.handle("/model provider/new-model")
                self.assertEqual(client.host.agent.llm.model, "provider/new-model")
                await client.handle("/export saved.json")
                with self.assertRaises(FileExistsError):
                    client._local_command("/export", "saved.json")
            await client.call(client.host.close)
            client.worker.shutdown()

        asyncio.run(scenario())
        saved = json.loads((self.root / "saved.json").read_text())
        self.assertEqual(saved["model"], "provider/new-model")
        self.assertEqual(saved["messages"][0]["content"], "inspect")

    def test_only_explicit_yes_allows_approval(self):
        client = self.client()

        async def scenario():
            for text, expected in (("y", True), (" YES ", True), ("", False), ("no", False), ("next task", False)):
                with patch("prompt_toolkit.output.defaults.create_output", return_value=DummyOutput()), patch("prompt_toolkit.PromptSession.prompt_async", AsyncMock(return_value=text)):
                    self.assertEqual(await client._ask_approval("bash", {"command": "echo ok"}, "shell"), expected)

        try:
            asyncio.run(scenario())
        finally:
            client.worker.shutdown()

    def test_always_covers_routine_shell_but_not_destructive_commands(self):
        from repopilot.agent.permission import DESTRUCTIVE_REASON, SHELL_REASON
        client = self.client()
        client.terminal = True
        client.host = SimpleNamespace(agent=SimpleNamespace(interactive=True))
        prompted = []

        def prompt(coroutine, loop):
            from concurrent.futures import Future
            coroutine.close()
            prompted.append(True)
            declined = Future()
            declined.set_result(False)
            return declined

        async def always(command, reason):
            with patch("prompt_toolkit.output.defaults.create_output", return_value=DummyOutput()), patch("prompt_toolkit.PromptSession.prompt_async", AsyncMock(return_value=" A ")):
                return await client._ask_approval("bash", {"command": command}, reason)

        try:
            self.assertTrue(asyncio.run(always("ls -la", SHELL_REASON)))
            self.assertIn("Allowed for this session: shell commands", self.output.getvalue())
            with patch("asyncio.run_coroutine_threadsafe", prompt):
                # Agents rarely repeat a command verbatim: one grant covers routine shell.
                self.assertTrue(client._approve("bash", {"command": "find docs -name '*.md' | head"}, SHELL_REASON))
                self.assertEqual(prompted, [])
                # Destructive commands still ask, and "always" only remembers that exact one.
                self.assertFalse(client._approve("bash", {"command": "rm build.log"}, DESTRUCTIVE_REASON))
                self.assertEqual(prompted, [True])
            self.assertTrue(asyncio.run(always("rm build.log", DESTRUCTIVE_REASON)))
            with patch("asyncio.run_coroutine_threadsafe", prompt):
                self.assertTrue(client._approve("bash", {"command": "rm build.log"}, DESTRUCTIVE_REASON))
                self.assertFalse(client._approve("bash", {"command": "rm other.log"}, DESTRUCTIVE_REASON))
                self.assertEqual(prompted, [True, True])
        finally:
            client.worker.shutdown()

    def test_permission_panel_shows_reason_and_grant_scope(self):
        self.ui.permission("bash", {"command": "pytest -q"}, "shell commands need approval in manual mode")
        self.ui.permission("bash", {"command": "rm a.log"}, "potentially destructive shell command")
        text = " ".join(self.output.getvalue().split())
        self.assertIn("Shell commands need approval in manual mode", text)
        self.assertIn("a all shell commands this session", text)
        self.assertIn("a this exact command this session", text)
        self.assertIn("$ rm a.log", text)

    def test_answer_headings_are_flush_left_and_code_is_carded(self):
        self.ui.answer("## Changes\n\nText\n\n```diff\n-old\n+new\n```")
        lines = self.output.getvalue().splitlines()
        heading = next(line for line in lines if "Changes" in line)
        self.assertEqual(heading.strip(), "Changes")
        self.assertLess(heading.index("Changes"), 4)
        self.assertTrue(any(line.strip() == "+new" for line in lines))

    def test_plan_shows_every_item_with_symbols(self):
        todos = "\n".join([f"[x] done {n}" for n in range(4)] + ["[>] doing", "[ ] next", "[ ] last"])
        self.ui.event("tool_start", name="todo_write", args={})
        self.ui.event("tool_end", name="todo_write", args={}, output=todos)
        text = self.output.getvalue()
        self.assertIn("✓ done 0", text)
        self.assertIn("▸ doing", text)
        self.assertIn("○ last", text)
        self.assertNotIn("[ ]", text)
        self.assertNotIn("--verbose", text)

    def test_agent_shell_command_renders_terminal_box(self):
        args = {"command": "pytest -q"}
        self.ui.event("tool_start", name="bash", args=args)
        output = "Command: pytest -q\nExit code: 1\n" + "\n".join(f"line {n}" for n in range(12))
        self.ui.event("tool_end", name="bash", args=args, output=output)
        text = self.output.getvalue()
        self.assertIn("Terminal", text)
        self.assertIn("$ pytest -q", text)
        self.assertIn("exit 1", text)
        self.assertIn("4 earlier lines", text)
        self.assertIn("line 11", text)
        self.assertNotIn("Command: pytest", text)

    def test_bang_runs_in_repository_and_reaches_next_message(self):
        seen = []

        class Recorder(ScriptedLLM):
            def chat(self, messages, schemas):
                seen.append(str(messages))
                return super().chat(messages, schemas)

        client = self.client(Recorder())

        async def scenario():
            await client.handle("!git status --short && echo marker-42")
            await client.handle("what changed?")

        try:
            asyncio.run(scenario())
        finally:
            client.worker.shutdown()
        text = self.output.getvalue()
        self.assertIn("$ git status --short && echo marker-42", text)
        self.assertIn("marker-42", text)
        self.assertIn("exit 0", text)
        self.assertTrue(any("marker-42" in str(message) and "what changed?" in str(message) for message in seen))
        self.assertEqual(client.shell_notes, [])

    def test_composer_hides_terminal_mode_bang(self):
        from prompt_toolkit.document import Document
        from prompt_toolkit.layout.processors import TransformationInput
        from repopilot.cli.composer import HideLeadingBang
        document = Document("!ls")
        result = HideLeadingBang().apply_transformation(
            TransformationInput(None, document, 0, lambda i: i, [("", "!ls")], 80, 1))
        self.assertEqual("".join(text for _, text in result.fragments), "ls")
        self.assertEqual(result.display_to_source(0), 1)

    def test_long_paste_folds_to_placeholder_and_expands_on_send(self):
        from prompt_toolkit import PromptSession
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output import DummyOutput
        client = self.client()
        log = "\n".join(f"Traceback line {n}" for n in range(40))
        try:
            with create_pipe_input() as pipe:
                with patch("prompt_toolkit.PromptSession", lambda **kw: PromptSession(input=pipe, output=DummyOutput(), **kw)):
                    prompt = client._make_session()
                pipe.send_text("why? \x1b[200~" + log + "\x1b[201~\r")
                text = prompt.prompt()
        finally:
            client.worker.shutdown()
        self.assertEqual(text, "why? [Pasted #1 · 40 lines]")
        self.assertEqual(client.expand_pastes(text), "why? " + log)

    def test_long_tasks_notify_when_done(self):
        client = self.client()
        client.terminal = True
        try:
            with patch("repopilot.cli.app.NOTIFY_AFTER", 0), patch.object(self.ui, "notify") as notify:
                asyncio.run(client.handle("inspect"))
            notify.assert_called_once()
            self.assertIn("RepoPilot finished", notify.call_args[0][0])
        finally:
            client.worker.shutdown()

    def test_notify_sends_bell_only_to_real_terminals(self):
        stream = io.StringIO()
        TerminalUI(Console(file=stream, force_terminal=True, width=80), animate=False).notify("done")
        self.assertIn("\a\x1b]9;done\x07", stream.getvalue())
        self.ui.notify("done")
        self.assertNotIn("\a", self.output.getvalue())
        with patch.dict(os.environ, {"REPOPILOT_NOTIFY": "0"}):
            quiet = io.StringIO()
            TerminalUI(Console(file=quiet, force_terminal=True, width=80), animate=False).notify("done")
        self.assertEqual(quiet.getvalue(), "")

    def test_welcome_fits_narrow_terminals(self):
        from repopilot.cli.ui import display_model, display_path
        home = str(Path.home())
        self.assertEqual(display_path(home + "/a/b", 40), "~/a/b")
        self.assertEqual(display_path("/data/very/long/workspace/tanxinyu/RL", 20), "/…/tanxinyu/RL")
        self.assertEqual(display_model("deepseek/deepseek/deepseek-v4.1-flash", 60), "deepseek/deepseek-v4.1-flash")
        self.assertEqual(display_model("provider/family/a-very-long-model-name", 20), "a-very-long-model-name")
        narrow = io.StringIO()
        TerminalUI(Console(file=narrow, width=56, color_system=None), animate=False).welcome(
            "/data/very/long/workspace/tanxinyu/RL", "deepseek/deepseek/deepseek-v4.1-flash", "main")
        self.assertNotIn("█", narrow.getvalue())
        self.assertIn("deepseek/deepseek-v4.1-flash", narrow.getvalue())

    def test_markdown_tables_get_a_grid_and_keep_alignment(self):
        self.ui.answer("| Name | Count |\n|:---|---:|\n| alpha | 1 |\n| beta | 22 |")
        text = self.output.getvalue()
        self.assertTrue("╭" in text or "┌" in text, text)
        self.assertTrue("├" in text or "┡" in text, text)
        row = next(line for line in text.splitlines() if "alpha" in line)
        self.assertRegex(row, r"│ alpha +│ +1 │")

    def test_edit_preview_reports_file_line_numbers(self):
        target = self.root / "lines.py"
        target.write_text("".join(f"line {n}\n" for n in range(1, 21)))
        self.ui.workspace = self.root
        args = {"path": "lines.py", "old_text": "line 12\n", "new_text": "line twelve\n"}
        self.ui.event("tool_start", name="edit_file", args=args)
        target.write_text(target.read_text().replace("line 12\n", "line twelve\n"))
        self.ui.event("tool_end", name="edit_file", args=args, output="Edited lines.py")
        text = self.output.getvalue()
        self.assertIn("● Edit  lines.py · 1 addition, 1 removal", text)
        self.assertRegex(text, r"12 -line 12")
        self.assertRegex(text, r"12 \+line twelve")
        self.assertNotIn("+++", text)

    def test_ctrl_c_at_approval_stops_without_approving(self):
        from prompt_toolkit import PromptSession
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output import DummyOutput
        client = self.client()
        client.busy = True
        try:
            with create_pipe_input() as pipe:
                pipe.send_text("\x03")
                with patch("prompt_toolkit.PromptSession", lambda **kw: PromptSession(input=pipe, output=DummyOutput(), **kw)):
                    self.assertFalse(asyncio.run(client._ask_approval("bash", {"command": "echo ok"}, "shell")))
            self.assertTrue(client.cancellation.is_set())
        finally:
            client.worker.shutdown()

    def test_tab_then_enter_in_one_input_batch(self):
        from prompt_toolkit import PromptSession
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output import DummyOutput
        client = self.client()
        try:
            with create_pipe_input() as pipe:
                def session(**kwargs):
                    return PromptSession(input=pipe, output=DummyOutput(), **kwargs)
                with patch("prompt_toolkit.PromptSession", session):
                    prompt = client._make_session()
                pipe.send_text("/sta\t\r")
                self.assertEqual(prompt.prompt(), "/status")
        finally:
            client.worker.shutdown()

    def test_demo_does_not_build_agent(self):
        with patch("repopilot.factory.build_agent", side_effect=AssertionError("Unexpected agent")), patch("sys.stdout", self.output):
            run_demo(self.root, plain=True)
        self.assertIn("OFFLINE DEMO", self.output.getvalue())

    def test_ctrl_c_cancels_draft_without_keyboard_interrupt(self):
        from prompt_toolkit import PromptSession
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output import DummyOutput
        client = self.client()
        try:
            with create_pipe_input() as pipe:
                with patch("prompt_toolkit.PromptSession", lambda **kw: PromptSession(input=pipe, output=DummyOutput(), **kw)):
                    prompt = client._make_session()
                pipe.send_text("discard this draft\x03")
                self.assertIsNone(prompt.prompt())
        finally:
            client.worker.shutdown()

    def test_interrupt_returns_130_and_allows_the_next_task(self):
        from repopilot.llm.cancellation import RequestCancelled
        entered = threading.Event()

        class WaitingLLM(ScriptedLLM):
            first = True

            def chat(self, messages, tools):
                if self.first:
                    self.first = False
                    entered.set()
                    if not client.cancellation.wait(2):
                        raise TimeoutError("The turn was not interrupted")
                    raise RequestCancelled()
                return reply()

        client = self.client(WaitingLLM())

        async def scenario():
            task = asyncio.create_task(client.task("wait for the provider"))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                client.interrupt()
                self.assertEqual(await asyncio.wait_for(task, 1), 130)
                self.assertFalse(client.busy)
                self.assertFalse(client.cancellation.is_set())
                self.assertEqual(await client.task("next task"), 0)
                self.assertIn("Interrupted", self.output.getvalue())
                self.assertNotIn("RequestCancelled", self.output.getvalue())
            finally:
                client.cancellation.set()
                await asyncio.gather(task, return_exceptions=True)
                if client.host:
                    await client.call(client.host.close)

        try:
            asyncio.run(scenario())
        finally:
            client.worker.shutdown()

    @unittest.skipIf(os.name == "nt", "POSIX terminal signal handling")
    def test_sigint_handler_survives_the_input_prompt(self):
        from prompt_toolkit import PromptSession
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output import DummyOutput
        client = self.client()

        async def scenario():
            loop = asyncio.get_running_loop()
            previous = signal.getsignal(signal.SIGINT)
            loop.add_signal_handler(signal.SIGINT, client.interrupt)

            async def handle(text):
                self.assertEqual(text, "start work")
                client.busy = True
                os.kill(os.getpid(), signal.SIGINT)
                self.assertTrue(await asyncio.to_thread(client.cancellation.wait, .5))
                return False

            try:
                with create_pipe_input() as pipe:
                    pipe.send_text("start work\r")
                    with patch("prompt_toolkit.PromptSession", lambda **kw: PromptSession(input=pipe, output=DummyOutput(), **kw)), \
                            patch.object(client, "handle", side_effect=handle):
                        await asyncio.wait_for(client._interactive(), 2)
            finally:
                client.busy = False
                loop.remove_signal_handler(signal.SIGINT)
                signal.signal(signal.SIGINT, previous)

        try:
            asyncio.run(scenario())
        finally:
            client.worker.shutdown()

    def test_frame_contains_the_real_input_and_history_search(self):
        from prompt_toolkit import PromptSession
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output import DummyOutput
        from prompt_toolkit.layout import walk
        from prompt_toolkit.layout.controls import BufferControl
        client = self.client()
        try:
            with create_pipe_input() as pipe:
                with patch("prompt_toolkit.PromptSession", lambda **kw: PromptSession(input=pipe, output=DummyOutput(), **kw)):
                    prompt = client._make_session()
                controls = [window.content for window in walk(prompt.composer_frame.__pt_container__())
                            if hasattr(window, "content") and isinstance(window.content, BufferControl)]
                self.assertTrue(any(control.buffer is prompt.default_buffer for control in controls))
                self.assertGreaterEqual(prompt.composer_frame.__pt_container__().preferred_width(80).max, 80)
                pipe.send_text("/status\r")
                self.assertEqual(prompt.prompt(), "/status")
                async def search_history():
                    task = asyncio.create_task(prompt.prompt_async())
                    await asyncio.sleep(.05)
                    pipe.send_text("\x12/status")
                    await asyncio.sleep(.05)
                    pipe.send_text("\r")
                    await asyncio.sleep(.05)
                    pipe.send_text("\r")
                    return await asyncio.wait_for(task, 1)
                self.assertEqual(asyncio.run(search_history()), "/status")
        finally:
            client.worker.shutdown()


class EventTests(unittest.TestCase):
    def test_cancellation_during_approval_prevents_write(self):
        cancelled = threading.Event()

        class OneToolLLM:
            def chat(self, messages, tools):
                return reply([call("write", "write_file", {"path": "blocked.txt", "content": "no"})])

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = ToolRegistry()
            registry.register(WriteFileTool(root))
            hooks = HookManager()
            hooks.register("PreToolUse", lambda *args: cancelled.set())
            messages = [{"role": "user", "content": "work"}]
            result = Agent(OneToolLLM(), registry, hooks, cancellation=cancelled, events=lambda *a, **kw: None).run(messages)
            self.assertIn("Interrupted", result)
            self.assertFalse((root / "blocked.txt").exists())
            self.assertEqual(messages[-1]["content"], "Cancelled before execution")

    def test_cancellation_closes_tool_batch_without_writes(self):
        cancelled = threading.Event()

        class CancelLLM:
            def chat(self, messages, tools):
                cancelled.set()
                return reply([call("one", "write_file", {"path": "one.txt", "content": "one"}),
                              call("two", "write_file", {"path": "two.txt", "content": "two"})])

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = ToolRegistry()
            registry.register(WriteFileTool(root))
            events = []
            messages = [{"role": "user", "content": "work"}]
            agent = Agent(CancelLLM(), registry, events=lambda event, **data: events.append((event, data)), cancellation=cancelled)
            self.assertIn("Interrupted", agent.run(messages))
            self.assertEqual([m["tool_call_id"] for m in messages if m["role"] == "tool"], ["one", "two"])
            self.assertFalse((root / "one.txt").exists())
            self.assertFalse((root / "two.txt").exists())
            self.assertEqual(len([e for e, d in events if e == "tool_end"]), 2)

    def test_invalid_tool_arguments_render_as_errors(self):
        class InvalidLLM:
            turns = 0

            def chat(self, messages, tools):
                self.turns += 1
                return reply([call("bad", "edit_file", [])]) if self.turns == 1 else reply()

        output = io.StringIO()
        ui = TerminalUI(Console(file=output, color_system=None), animate=False)
        messages = [{"role": "user", "content": "work"}]
        self.assertEqual(Agent(InvalidLLM(), ToolRegistry(), events=ui.event).run(messages), "Done.")
        self.assertIn("invalid tool call", output.getvalue())

    def test_configuration_display_redacts_credentials(self):
        self.assertEqual(safe_url("https://user:secret@example.com/v1?api_key=secret"), "https://example.com/v1")
        with patch.dict(os.environ, {"OPENAI_API_KEY": "private-test-key"}):
            self.assertNotIn("private-test-key", error_message(RuntimeError("bad private-test-key")))
