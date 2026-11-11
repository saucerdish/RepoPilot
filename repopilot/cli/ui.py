"""Rich rendering for the scrollback-friendly terminal client."""

import difflib
import json
import os
import re
import threading
import time
from pathlib import Path

from rich import box
from rich.console import Console, Group
from rich.markdown import CodeBlock, Heading, Markdown, TableElement
from rich.padding import Padding
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from repopilot.agent.permission import session_grant

from . import VERSION
from .brand import ICON_WIDTH, icon_mark
from .diffview import DiffView, change_counts, shift_hunk
from .theme import SHIMMER_COLORS, TERMINAL_COLORS, palette_for


THEME = palette_for().rich_theme()

COMMANDS = {
    "/help": "Commands and keyboard shortcuts",
    "/status": "Repository, model, permissions and session usage",
    "/model": "Show or switch model: /model <model-id>",
    "/mode": "Switch approvals: /mode manual | auto",
    "/theme": "Switch appearance: /theme auto | light | dark",
    "/diff": "Review tracked changes, including staged changes",
    "/tasks": "View the plan and persistent task list",
    "/goal": "Show, set or clear acceptance criteria",
    "/compact": "Summarize the current conversation",
    "/clear": "Start a fresh conversation; preserve files and goals",
    "/export": "Save the conversation: /export [path.json]",
    "/exit": "Close the session and managed processes",
}

TOOL_LABELS = {
    "read_file": "Read", "write_file": "Write", "edit_file": "Edit",
    "glob": "Search", "bash": "Shell", "todo_write": "Plan",
    "task": "Delegate", "Workflow": "Workflow", "load_skill": "Skill",
    "recall_memory": "Recall", "save_memory": "Remember",
}


ICON_MIN_WIDTH = 72


def display_path(path, limit):
    """~ for the home directory, then keep the tail: ~/…/tanxinyu/RL."""
    text, home = str(path), str(Path.home())
    if text == home or text.startswith(home + "/"):
        text = "~" + text[len(home):]
    if len(text) <= limit:
        return text
    parts = text.split("/")
    tail = parts[-1]
    for part in reversed(parts[1:-1]):
        if len(parts[0]) + 3 + len(part) + 1 + len(tail) > limit:
            break
        tail = part + "/" + tail
    short = parts[0] + "/…/" + tail
    return short if len(short) <= limit else "…" + text[-max(1, limit - 1):]


def display_model(model, limit):
    """Drop repeated provider segments; fall back to the model's own name."""
    parts = [part for index, part in enumerate(model.split("/"))
             if part and (index == 0 or part != model.split("/")[index - 1])]
    short = "/".join(parts) or model
    return short if len(short) <= limit or not parts else parts[-1]


def parse_shell_output(command, output):
    """Split the bash tool's "Command:/Exit code:" preamble from the output."""
    if output.startswith("Command: "):
        output = output.partition("\n")[2]
    match = re.match(r"Exit code: (\S+)\n?", output)
    if match:
        return command, match.group(1), output[match.end():]
    if output.startswith("Error: Timeout"):
        return command, "timeout", output
    return command, None, output


def _blend(low, high, weight):
    """Mix two #rrggbb colours; weight 0 gives low, 1 gives high."""
    a = [int(low[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(high[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * weight):02x}" for x, y in zip(a, b))


def format_elapsed(seconds):
    seconds = int(seconds)
    return f"{seconds // 60}m {seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"


class Elapsed:
    """Spinner text: a light band sweeps across the label, then a timer.

    Rich re-renders this on every refresh (about 12 times a second), so the
    shimmer and the counter animate without a thread of their own.
    """

    BAND = 3.0      # half-width of the highlight, in characters
    SPEED = 16.0    # characters per second
    PAUSE = 8       # quiet characters between sweeps

    def __init__(self, message, palette_name=None, clock=time.monotonic):
        self.label = message + "…" if message == "Thinking" else message
        self.colors = SHIMMER_COLORS.get(palette_name)
        self.clock = clock
        self.started = clock()

    def _shimmer(self, elapsed):
        text = Text(no_wrap=True, overflow="ellipsis")
        cycle = len(self.label) + 2 * self.BAND + self.PAUSE
        centre = (elapsed * self.SPEED) % cycle - self.BAND
        for index, char in enumerate(self.label):
            weight = max(0.0, 1 - abs(index - centre) / self.BAND)
            if self.colors:
                text.append(char, "bold " + _blend(*self.colors, weight) if weight else "bold " + self.colors[0])
            else:
                # ANSI-only: the band is the terminal foreground, the rest the accent.
                text.append(char, "bold" if weight > .4 else "brand")
        return text

    def __rich__(self):
        elapsed = self.clock() - self.started
        text = self._shimmer(elapsed)
        if elapsed >= 1:
            text.append("  " + format_elapsed(elapsed), "muted")
        text.append("  ·  Ctrl+C to stop", "muted")
        return text


class LeftHeading(Heading):
    """Rich centres headings; in a transcript they read better flush left."""

    def __rich_console__(self, console, options):
        self.text.justify = "left"
        if self.tag == "h1":
            yield Text("")
        yield self.text


PLAN_MARKERS = {"[x]": ("✓", "success", "muted"), "[>]": ("▸", "accent", "heading"), "[ ]": ("○", "muted", "text")}


class TerminalUI:
    def __init__(self, console=None, plain=False, verbose=False, animate=True, theme="auto"):
        self.mode = theme
        self.plain = plain
        self.palette = palette_for(theme)
        self.console = console or Console(no_color=plain,
                                          force_terminal=False if plain else None,
                                          highlight=False)
        self.console.push_theme(self.palette.rich_theme())
        self.verbose = verbose
        self.animate = animate and self.console.is_terminal and not plain
        self.status = None
        self.tool_count = 0
        self._lock = threading.RLock()
        self._started = {}
        self._edit_lines = {}
        # What was printed last: "start" (a blank line already separates),
        # "line" (a one-line tool summary) or "block". See _open().
        self._last = "start"
        ui = self

        class ThemedCodeBlock(CodeBlock):
            def __rich_console__(self, console, options):
                yield ui.code_card(str(self.text).rstrip(), self.lexer_name)

        class ThemedTable(TableElement):
            """Rich's default is a lone heavy rule under the header; draw a
            light rounded grid with zebra rows so wrapped cells stay readable."""

            def __rich_console__(self, console, options):
                surface = ui.palette.surface
                table = Table(box=box.ROUNDED, border_style="markdown.table.border",
                              header_style="markdown.table.header", padding=(0, 1),
                              row_styles=None if surface == "default" or ui.plain else ["", "on " + surface])
                if self.header is not None and self.header.row is not None:
                    for cell in self.header.row.cells:
                        table.add_column(cell.content, overflow="fold",
                                         justify="left" if cell.justify == "default" else cell.justify)
                if self.body is not None:
                    for row in self.body.rows:
                        table.add_row(*(cell.content for cell in row.cells))
                yield table

        self._markdown = type("ThemedMarkdown", (Markdown,), {"elements": {
            **Markdown.elements, "heading_open": LeftHeading, "table_open": ThemedTable,
            "fence": ThemedCodeBlock, "code_block": ThemedCodeBlock}})
        # Set by the client so edit previews can locate the edited lines.
        self.workspace = None

    def set_theme(self, mode):
        palette = palette_for(mode)
        self.stop()
        self.console.pop_theme()
        self.console.push_theme(palette.rich_theme())
        self.mode, self.palette = mode, palette

    def markdown(self, content):
        return self._markdown(str(content), code_theme=self.palette.syntax)

    def code_card(self, code, lexer):
        """Fenced code on the palette surface, so blocks match the theme."""
        lexer = "diff" if lexer in ("diff", "patch", "udiff") else lexer
        surface = self.palette.surface
        return Padding(self.syntax(code, lexer), (1, 2), expand=True,
                       style="" if surface == "default" else "on " + surface)

    def syntax(self, source, lexer):
        if lexer == "diff":
            return DiffView(source)
        if self.palette.name == "native":
            return Text(source, style="text")
        return Syntax(source, lexer, theme=self.palette.syntax,
                      background_color=self.palette.surface, word_wrap=True)

    @property
    def width(self):
        return min(self.console.width, 108)

    def welcome(self, workspace, model, branch, configured=True, demo=False, permission_mode="manual"):
        workspace = Path(workspace)
        # The plane needs room; on narrow terminals the text matters more.
        show_icon = self.width >= ICON_MIN_WIDTH
        room = self.width - 2 - (ICON_WIDTH + 2 if show_icon else 0)
        details = Group(
            Text.assemble(("RepoPilot", "brand"), (f"  v{VERSION}", "muted"), no_wrap=True, overflow="ellipsis"),
            Text.assemble((workspace.name or str(workspace), "text"), (" / ", "muted"),
                          (branch, "brand"), no_wrap=True, overflow="ellipsis"),
            Text(display_path(workspace, room), style="muted", no_wrap=True, overflow="ellipsis"),
            Text.assemble(("model  ", "muted"), (display_model(model, room - 7) if model else "not configured", "text"),
                          no_wrap=True, overflow="ellipsis"),
            Text.assemble((permission_mode, "brand"),
                          (" · no approval prompts" if permission_mode == "auto" else " · ask before running", "muted"),
                          no_wrap=True, overflow="ellipsis"),
            Text.assemble(("/help", "brand"), (" commands · ", "muted"),
                          ("/mode", "brand"), (" permissions", "muted"),
                          no_wrap=True, overflow="ellipsis"),
        )
        header = Table.grid(expand=True, padding=(0, 2))
        if show_icon:
            header.add_column(width=ICON_WIDTH, no_wrap=True)
        header.add_column(ratio=1, min_width=1)
        header.add_row(*([icon_mark(None if self.plain else self.palette)] if show_icon else []), details)
        self.console.print()
        self.console.print(Padding(header, (0, 1)), width=self.width)
        if demo:
            self.console.print(Text("  OFFLINE DEMO · NO API REQUESTS", style="brand"))
        if not configured and not demo:
            self.notice("API key or model missing. Use --env-file /path/to/.env and --model <id> before sending a task.")
        self.console.print()
        self._last = "start"

    def thinking(self, message="Thinking"):
        with self._lock:
            if self.animate and self.status is None:
                self.status = self.console.status(Elapsed(message, self.palette.name), spinner="dots",
                                                  spinner_style="brand")
                self.status.start()

    def stop(self):
        with self._lock:
            if self.status is not None:
                self.status.stop()
                self.status = None

    def _open(self, kind="block"):
        """Start an item: exactly one blank line between items, while a run of
        one-line tool summaries ("line") stays together as a list."""
        if self._last != "start" and not (kind == "line" and self._last == "line"):
            self.console.print()
        self._last = kind

    def notify(self, message):
        """Bell plus an OSC 9 desktop notification (iTerm2, WezTerm, kitty…);
        terminals without notifications ignore the sequence."""
        if self.plain or not self.console.is_terminal or os.environ.get("REPOPILOT_NOTIFY", "1") == "0":
            return
        message = re.sub(r"[\x00-\x1f\x7f]", " ", message)
        self.console.file.write(f"\a\x1b]9;{message}\x07")
        self.console.file.flush()

    def notice(self, content, error=False):
        # Notices attach to whatever they comment on, without a gap.
        self.stop()
        self.console.print(Text.assemble(("  ! " if error else "  · ", "error" if error else "accent"),
                                         (str(content), "text" if error else "muted")))
        if self._last == "start":
            self._last = "line"

    def user(self, content):
        """The sent message as a quiet one-line bubble, not the composer frame."""
        self.stop()
        self._open()
        message = Text.assemble(("❯ ", "brand"), (str(content), "text"))
        surface = self.palette.surface
        if surface == "default" or self.plain:
            self.console.print(Padding(message, (0, 2)), width=self.width)
        else:
            bubble = Padding(message, (0, 1), style="on " + surface, expand=True)
            self.console.print(Padding(bubble, (0, 2)), width=self.width)

    def event(self, event, **data):
        with self._lock:
            if event == "model_start":
                self.thinking()
            elif event == "model_end":
                self.stop()
            elif event == "assistant":
                self.stop()
                self._open()
                self.console.print(Padding(self.markdown(data["content"]), (0, 2)))
            elif event == "tool_start":
                self.stop()
                self.tool_count += 1
                name, args = data["name"], data["args"]
                self._started[(threading.get_ident(), name)] = time.monotonic()
                if name == "edit_file":
                    # Locate old_text before the edit runs: difflib only sees
                    # the snippet, so its hunks would always start at line 1.
                    self._edit_lines[(threading.get_ident(), args.get("path"))] = self._line_before(args)
                # Header and result print together when the tool finishes, so
                # each call reads as one line; meanwhile the spinner names it.
                if name == "bash":
                    self.thinking("$ " + str(args.get("command", "")).replace("\n", " ")[:60])
                else:
                    label, detail = self._describe(name, args)
                    self.thinking(f"{label} {detail}".strip()[:72])
            elif event == "tool_end":
                self._tool_result(data["name"], data["args"], str(data["output"]))
            elif event == "notice":
                self.notice(data["content"])

    def _describe(self, name, args):
        label = TOOL_LABELS.get(name, name.replace("_", " ").title())
        detail = args.get("path") or args.get("command") or args.get("pattern") or args.get("name") or ""
        detail = str(detail).replace("\n", " ")
        if len(detail) > self.width - 30:
            detail = detail[:max(10, self.width - 33)] + "…"
        return label, detail

    def _tool_result(self, name, args, output):
        self.stop()
        started = self._started.pop((threading.get_ident(), name), None)
        seconds = time.monotonic() - started if started else 0
        offset = self._edit_lines.pop((threading.get_ident(), args.get("path")), 0) if name == "edit_file" else 0
        failed = output.lower().startswith(("error", "permission denied", "cancelled")) or bool(re.search(r"Exit code: (?!0\b)-?\d+", output))
        if name == "bash":
            command, code, body = parse_shell_output(str(args.get("command", "")), output)
            self.terminal(command, body, code, seconds, failed)
            return
        label, detail = self._describe(name, args)

        def header(summary="", kind="line"):
            self._open(kind)
            # Sub-second timings are noise; only slow tools earn a duration.
            extra = " · ".join(part for part in (summary, f"{seconds:.1f}s" if seconds >= 1 else "") if part)
            self.console.print(Text.assemble(("  ● ", "error" if failed else "brand"), (label, "heading"),
                                             ("  " + detail if detail else "", "muted"),
                                             ((" · " if detail else "  ") + extra if extra else "", "muted")))

        def block(renderable):
            self.console.print(Padding(renderable, (0, 4)))

        if failed:
            header(kind="block")
            block(Text("\n".join(output.splitlines()[:18 if self.verbose else 6])[:4000], style="error"))
        elif name == "todo_write":
            header(kind="block")
            block(self.plan(output))
        elif name == "edit_file":
            diff = "\n".join(difflib.unified_diff(args.get("old_text", "").splitlines(),
                                               args.get("new_text", "").splitlines(),
                                               fromfile=args.get("path", "before"), tofile=args.get("path", "after"), lineterm=""))
            lines = [shift_hunk(line, offset) for line in diff.splitlines()]
            added, removed = change_counts(diff)
            header(f"{added} addition{'s' * (added != 1)}, {removed} removal{'s' * (removed != 1)}", kind="block")
            view = [DiffView("\n".join(lines[:40]), compact=True)] if lines else []
            if len(lines) > 40:
                view.append(Text("… use /diff to review the full change", style="muted"))
            block(Group(*view))
        elif name in ("read_file", "glob") and not self.verbose:
            if name == "read_file":
                count = len(output.splitlines())
                summary = f"{count} line{'s' * (count != 1)}"
            else:
                paths = [line for line in output.splitlines() if not line.startswith("... (")]
                summary = "no matches" if output == "(no matches)" else f"{len(paths)} path{'s' * (len(paths) != 1)}"
                if "results may be incomplete" in output:
                    summary += " (stopped early)"
            header(summary)
        else:
            lines = output.splitlines()
            limit = 18 if self.verbose else 5
            if len(lines) > limit:
                lines = lines[:limit] + ["… --verbose for more tool output"]
            header(kind="block" if lines else "line")
            if lines:
                block(Text("\n".join(lines)[:4000], style="muted"))

    def terminal(self, command, output, code=None, seconds=0.0, failed=False):
        """A small terminal window: title bar, the command, its output tail and exit status."""
        self.stop()
        self._open()
        lines = output.rstrip("\n").splitlines()
        limit = 40 if self.verbose else 8
        body = [Text.assemble(("$ ", "accent"), (command, "heading"))]
        if len(lines) > limit:
            body.append(Text(f"… {len(lines) - limit} earlier lines (--verbose shows more)", style="muted"))
            lines = lines[-limit:]
        if lines:
            body.append(Text("\n".join(lines)[-4000:], style="error" if failed else "muted"))
        status = [] if code is None else ["exit " + str(code)]
        if seconds >= 1:
            status.append(f"{seconds:.1f}s")
        status = Text(" · ".join(status), style="error" if failed else "muted")
        colors = TERMINAL_COLORS.get(self.palette.name)
        if colors is None or self.plain:
            window = Panel(Group(*body), title=Text(" Terminal ", style="accent"), title_align="left",
                           subtitle=Text(" " + status.plain + " ", style=status.style) if status.plain else None,
                           subtitle_align="right", box=box.ROUNDED,
                           border_style="error" if failed else "border", padding=(0, 1))
        else:
            chrome, background = colors
            bar = Table.grid(expand=True)
            bar.add_column(ratio=1)
            bar.add_column(no_wrap=True, justify="right")
            bar.add_row(Text.assemble(("●", "#ff5f57"), (" ", ""), ("●", "#febc2e"), (" ", ""), ("●", "#28c840"),
                                      ("   Terminal", "muted")), status)
            window = Group(Padding(bar, (0, 1), style="on " + chrome, expand=True),
                           Padding(Group(*body), (1, 2), style="on " + background, expand=True))
        self.console.print(Padding(window, (0, 2)), width=self.width)

    def plan(self, output):
        """Checklist with symbols and hanging indents; never truncated."""
        table = Table.grid(padding=(0, 1))
        table.add_column(no_wrap=True)
        table.add_column(ratio=1)
        for line in output.splitlines():
            marker, content = line[:3], line[4:]
            if marker not in PLAN_MARKERS:
                marker, content = None, line
            symbol, symbol_style, text_style = PLAN_MARKERS.get(marker, ("·", "muted", "text"))
            table.add_row(Text(symbol, style=symbol_style), Text(content, style=text_style))
        return table

    def _line_before(self, args):
        """Lines in the file before old_text, or 0 when it cannot be found."""
        path, old_text = args.get("path"), args.get("old_text")
        if not isinstance(path, str) or not isinstance(old_text, str) or not old_text:
            return 0
        target = Path(path)
        if not target.is_absolute():
            if self.workspace is None:
                return 0
            target = Path(self.workspace) / target
        try:
            if target.stat().st_size > 5_000_000:
                return 0
            content = target.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return 0
        index = content.find(old_text)
        return content.count("\n", 0, index) if index >= 0 else 0

    def permission(self, name, args, reason):
        """A compact approval card: what will run, why it asks, and the keys."""
        self.stop()
        self._open()
        if name == "bash":
            action = Text.assemble(("$ ", "accent"), (str(args.get("command", "")), "heading"))
        else:
            action = self.syntax(json.dumps(args, ensure_ascii=False, indent=2), "json")
        why = str(reason or "").strip()
        keys = Text.assemble(("y", "accent"), (" allow once  ·  ", "muted"),
                             ("a", "accent"), (" " + session_grant(name, args, reason)[2] + " this session  ·  ", "muted"),
                             ("n", "accent"), (" decline", "muted"))
        self.console.print(Padding(Panel(Group(action, keys),
                                         title=Text(" Allow " + TOOL_LABELS.get(name, name) + "? ", style="accent"),
                                         title_align="left",
                                         subtitle=Text(" " + why[:1].upper() + why[1:] + " ", style="muted") if why else None,
                                         subtitle_align="right", box=box.ROUNDED, border_style="accent",
                                         padding=(0, 1)), (0, 2)), width=self.width)

    def answer(self, content, seconds=None, metrics=None, goal=None):
        self.stop()
        if content:
            self._open()
            self.console.print(Text("  ◈ RepoPilot", style="brand"))
            self.console.print(Padding(self.markdown(content), (0, 2)))
        if seconds is not None:
            parts = [f"{seconds:.1f}s", f"{self.tool_count} tool{'s' * (self.tool_count != 1)}"]
            if metrics:
                parts += [f"{metrics.get('api_calls', 0)} requests", f"{metrics.get('total_tokens', 0):,} tokens"]
            if goal:
                parts.append("goal " + goal["status"])
            self._open()
            self.console.print(Text("  ── " + " · ".join(parts), style="muted"))
            # The composer comes next: leave one blank line and start afresh.
            self.console.print()
            self._last = "start"
        self.tool_count = 0

    def help(self):
        self._open()
        table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2), width=self.width)
        table.add_column(style="brand", no_wrap=True)
        table.add_column(style="text")
        for command, description in COMMANDS.items():
            table.add_row(Text(command), Text(description))
        self.console.print(table)
        self.console.print(Text.assemble(("  !<command>", "brand"), ("  run a command in the repository's terminal; "
                                                                   "its output goes to the model with your next message", "muted")))
        self.console.print(Text("  Enter send · Alt+Enter / Ctrl+J new line · Ctrl+R search history", style="muted"))
        self.console.print(Text("  Ctrl+C clear draft / request stop · Ctrl+D exit · Tab complete", style="muted"))

    def details(self, title, values):
        self._open()
        table = Table.grid(padding=(0, 2))
        table.add_column(style="muted")
        table.add_column(style="text")
        for key, value in values.items():
            table.add_row(Text(str(key)), Text(str(value)))
        self.console.print(Panel(table, title=Text(title, style="brand"), border_style="border", width=self.width))

    def diff(self, content):
        self._open()
        if content.strip():
            self.console.print(self.syntax(content, "diff"))
        else:
            self.notice("No tracked changes. New, untracked files are listed in /status.")

    def composer_preview(self, workspace, model, branch):
        self.console.print()
        self.console.print(Panel(Group(Text.assemble(("❯ ", "brand"),
                                                     ("Describe a change, ask a question, or paste an error…", "muted")), Text("")),
                                 title=Text(" Message ", style="brand"), title_align="left",
                                 border_style="border", box=box.ROUNDED, padding=(0, 1), width=self.width))
        self.console.print(Text("  Enter send · Alt+Enter new line · ! terminal · Tab complete · ↑ history", style="muted"))
