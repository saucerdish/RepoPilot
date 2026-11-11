"""Deterministic UI tour. It never constructs an agent or calls an API."""

from rich.console import Console
from rich.terminal_theme import TerminalTheme

from repopilot.agent.permission import SHELL_REASON

from .ui import TerminalUI
from .theme import PALETTES

ANSWER = """Updated `slugify` to collapse runs of whitespace into a single hyphen.

| Input | Before | After |
| --- | --- | --- |
| `" Hello  World "` | `" hello  world "` | `"hello-world"` |
| `"RepoPilot"` | `"repopilot"` | `"repopilot"` |

**Demo result:** tests pass; the test file is unchanged."""


def run_demo(workspace, export_svg=None, plain=False, theme="auto", permission_mode="manual",
             title="RepoPilot · Offline terminal preview"):
    console = Console(record=bool(export_svg), width=100 if export_svg else None,
                      force_terminal=True if export_svg else None,
                      color_system="truecolor" if export_svg else "auto",
                      no_color=plain, highlight=False)
    ui = TerminalUI(console=console, plain=plain, animate=False,
                    theme="light" if export_svg and theme == "auto" else theme)
    ui.welcome(workspace, "deepseek-v4.1-flash", "main", demo=True, permission_mode=permission_mode)
    ui.user("Fix slugify so extra spaces become a single hyphen. Run the tests.")
    ui.event("assistant", content="I'll read the module and its test first.")
    for name, args, output in (("read_file", {"path": "module.py"}, "def slugify(text):\n    return text.lower()"),
                               ("glob", {"pattern": "tests/**/*.py"}, "tests/test_module.py")):
        ui.event("tool_start", name=name, args=args)
        ui.event("tool_end", name=name, args=args, output=output)
    plan = "[x] Read slugify and its test\n[>] Collapse whitespace into one hyphen\n[ ] Run the test suite"
    ui.event("tool_start", name="todo_write", args={})
    ui.event("tool_end", name="todo_write", args={}, output=plan)
    edit = {"path": "module.py", "old_text": "def slugify(text):\n    return text.lower()\n",
            "new_text": 'def slugify(text):\n    return "-".join(text.lower().split())\n'}
    ui.event("tool_start", name="edit_file", args=edit)
    ui.event("tool_end", name="edit_file", args=edit, output="Edited module.py")
    command = {"command": "python3 -m unittest discover -s tests -v"}
    if permission_mode == "manual":
        ui.permission("bash", command, SHELL_REASON)
        ui.notice("Demo preview: sample approval and output. No command is executed.")
    else:
        ui.notice("Auto mode: sample command approved automatically. No command is executed.")
    ui.event("tool_start", name="bash", args=command)
    ui.event("tool_end", name="bash", args=command,
             output="Exit code: 0\ntest_collapses_spaces (test_module.SlugTests) ... ok\n"
                    "test_lowercases (test_module.SlugTests) ... ok\n\nRan 2 tests in 0.001s\n\nOK")
    ui.answer(ANSWER, seconds=0.0, goal={"status": "completed (demo)"})
    ui.console.print("[muted]  OFFLINE DEMO · sample events only · /help to explore the live client[/muted]")
    ui.composer_preview(workspace, "deepseek-v4.1-flash", "main")
    if export_svg:
        save_svg(console, ui, export_svg, title)


def run_first_screen(workspace, export_svg, theme="light", title="RepoPilot · first screen"):
    """The client as it opens: welcome header and an empty composer."""
    console = Console(record=True, width=100, force_terminal=True, color_system="truecolor", highlight=False)
    ui = TerminalUI(console=console, animate=False, theme=theme)
    ui.welcome(workspace, "deepseek/deepseek/deepseek-v4.1-flash", "main")
    ui.composer_preview(workspace, "deepseek-v4.1-flash", "main")
    save_svg(console, ui, export_svg, title)


def save_svg(console, ui, export_svg, title):
    export_svg.parent.mkdir(parents=True, exist_ok=True)
    palette = ui.palette if ui.palette.name != "native" else PALETTES["light"]

    def rgb(value):
        return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))

    terminal_theme = TerminalTheme(rgb(palette.background), rgb(palette.foreground),
                                  [(23, 32, 51), (185, 28, 28), (22, 101, 52), (146, 64, 14),
                                   (29, 78, 216), (109, 40, 217), (14, 116, 144), (241, 245, 249)])
    console.save_svg(str(export_svg), title=title, theme=terminal_theme)
    source = export_svg.read_text(encoding="utf-8")
    export_svg.write_text(source.replace("<svg ", '<svg xml:space="preserve" ', 1), encoding="utf-8")
