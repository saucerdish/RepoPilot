"""Educational permission policy implemented as a PreToolUse hook."""

import json
import re
from pathlib import Path


DESTRUCTIVE_COMMAND = re.compile(r"(?i)(?:^|[;&|()\n])\s*(?:rm|del|erase|rmdir|rd)(?=\s|$|[;&|()])")
HARD_DENY = ("rm -rf /", "sudo", "shutdown", "reboot", "mkfs", "dd if=", "> /dev/sda")
SHELL_REASON = "shell commands need approval in manual mode"
DESTRUCTIVE_REASON = "potentially destructive shell command"


def session_grant(tool_name, args, reason):
    """What answering "always" covers: (key, description, short label).

    Routine shell commands rarely repeat verbatim, so one grant covers them
    all; destructive commands and other tools stay limited to the exact call.
    """
    if tool_name == "bash" and reason == SHELL_REASON:
        return ("bash", "*"), "shell commands (destructive ones still ask)", "all shell commands"
    if tool_name == "bash":
        command = str(args.get("command", ""))
        label = command if len(command) <= 60 else command[:59] + "…"
        return ("bash", command), "this exact command: " + label, "this exact command"
    key = (tool_name, json.dumps(args, sort_keys=True, ensure_ascii=False, default=str))
    return key, f"{tool_name} with these exact arguments", "these exact arguments"


class ApprovalMode:
    """Session policy shared with child agents so mode switches take effect."""

    def __init__(self, value="manual"):
        self.set(value)

    def set(self, value):
        if value not in ("manual", "auto"):
            raise ValueError("Mode must be manual or auto")
        self.value = value

    @property
    def description(self):
        return "no approval prompts" if self.value == "auto" else "ask before running"


class PermissionHook:
    def __init__(self, workspace: Path, ask=None, confirm_shell=False, allow_commands=()):
        self.workspace = workspace.resolve()
        self.ask = ask or self.ask_user
        self.confirm_shell = confirm_shell
        self.allow_commands = set(allow_commands)

    @staticmethod
    def ask_user(tool_name: str, args: dict, reason: str) -> bool:
        print(f"\nPermission requested: {reason}\nTool: {tool_name}\nArguments: {args}")
        try:
            return input("Allow this tool call? [y/N] ").strip().lower() in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False

    def __call__(self, tool_name: str, args: dict) -> str | None:
        if tool_name == "bash":
            command = args.get("command", "")
            if not isinstance(command, str):
                return "Permission denied: invalid command"
            lower = command.lower()
            for pattern in HARD_DENY:
                if pattern in lower:
                    return f"Permission denied: blocked command ({pattern})"
            if DESTRUCTIVE_COMMAND.search(command) or "chmod 777" in lower or "> /etc/" in lower:
                reason = DESTRUCTIVE_REASON
            elif self.confirm_shell and command not in self.allow_commands:
                reason = SHELL_REASON
            else:
                reason = None
            if reason and not self.ask(tool_name, args, reason):
                return "Permission denied by user"
        elif tool_name in ("spawn_teammate", "create_worktree"):
            if not self.ask(tool_name, args, "add a concurrent agent or create a task checkout"):
                return "Permission denied by user"
        elif tool_name in ("read_file", "write_file", "edit_file"):
            path = args.get("path")
            if not isinstance(path, str):
                return "Permission denied: invalid path"
            if not (self.workspace / path).resolve().is_relative_to(self.workspace):
                return "Permission denied: path outside repository"
        return None
