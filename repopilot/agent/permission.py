"""Educational permission policy implemented as a PreToolUse hook."""

import re
from pathlib import Path


DESTRUCTIVE_COMMAND = re.compile(r"(?i)(?:^|[;&|()\n])\s*(?:rm|del|erase|rmdir|rd)(?=\s|$|[;&|()])")
HARD_DENY = ("rm -rf /", "sudo", "shutdown", "reboot", "mkfs", "dd if=", "> /dev/sda")


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
            if self.confirm_shell and command not in self.allow_commands or DESTRUCTIVE_COMMAND.search(command) or "chmod 777" in lower or "> /etc/" in lower:
                if not self.ask(tool_name, args, "potentially destructive shell command"):
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
