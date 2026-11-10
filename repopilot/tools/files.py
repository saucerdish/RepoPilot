"""Workspace-scoped file tools for repository tasks."""

import os
import re
import time
from pathlib import Path

from .base import Tool

class WorkspaceTool(Tool):
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()

    def safe_path(self, path: str, write=False) -> Path:
        target = (self.workspace / path).resolve()
        if not target.is_relative_to(self.workspace):
            raise ValueError(f"Path escapes workspace: {path}")
        if write and any(part in (".git", ".repopilot") for part in target.relative_to(self.workspace).parts):
            raise ValueError("Agent file tools cannot overwrite Git or runtime metadata")
        return target


class ReadFileTool(WorkspaceTool):
    name = "read_file"
    description = "Read a UTF-8 file in the repository, optionally limiting lines."

    def schema(self):
        schema = super().schema()
        schema["function"]["parameters"].update({
            "properties": {"path": {"type": "string"}, "limit": {"type": "integer", "minimum": 1}},
            "required": ["path"],
        })
        return schema

    def run(self, path: str, limit: int | None = None) -> str:
        try:
            lines = self.safe_path(path).read_text(encoding="utf-8").splitlines()
            if limit is not None:
                if limit < 1:
                    return "Error: limit must be positive"
                omitted = max(0, len(lines) - limit)
                lines = lines[:limit]
                if omitted:
                    lines.append(f"... ({omitted} more lines)")
            return "\n".join(lines)
        except (OSError, UnicodeError, ValueError) as exc:
            return f"Error: {exc}"


class WriteFileTool(WorkspaceTool):
    name = "write_file"
    description = "Create or overwrite a UTF-8 file in the repository."

    def schema(self):
        schema = super().schema()
        schema["function"]["parameters"].update({
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        })
        return schema

    def run(self, path: str, content: str) -> str:
        try:
            target = self.safe_path(path, write=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"Wrote {len(content)} characters to {path}"
        except (OSError, ValueError) as exc:
            return f"Error: {exc}"


class EditFileTool(WorkspaceTool):
    name = "edit_file"
    description = "Replace one occurrence of exact text in a UTF-8 file."

    def schema(self):
        schema = super().schema()
        schema["function"]["parameters"].update({
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
            },
            "required": ["path", "old_text", "new_text"],
        })
        return schema

    def run(self, path: str, old_text: str, new_text: str) -> str:
        try:
            if not old_text:
                return "Error: old_text must not be empty"
            target = self.safe_path(path, write=True)
            content = target.read_text(encoding="utf-8")
            count = content.count(old_text)
            if count != 1:
                return f"Error: expected one occurrence in {path}, found {count}"
            target.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
            return f"Edited {path}"
        except (OSError, UnicodeError, ValueError) as exc:
            return f"Error: {exc}"


# Directories that are never worth searching and can hold huge trees.
SKIP_DIRS = {".git", ".repopilot", "node_modules", "__pycache__", ".venv", "venv", ".tox",
             ".mypy_cache", ".pytest_cache", ".ruff_cache", ".cache", ".ipynb_checkpoints"}
MAX_MATCHES = 200
TIME_BUDGET = 10.0


def glob_regex(pattern):
    """Translate a glob with ** into a regex over slash-separated paths."""
    out, index = [], 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif pattern[index] == "*":
            out.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            out.append("[^/]")
            index += 1
        elif pattern[index] == "[" and "]" in pattern[index + 1:]:
            close = pattern.index("]", index + 1)
            body = pattern[index + 1:close]
            out.append("[" + ("^" + body[1:] if body.startswith("!") else body) + "]")
            index = close + 1
        else:
            out.append(re.escape(pattern[index]))
            index += 1
    return re.compile("".join(out) + r"\Z")


class GlobTool(WorkspaceTool):
    name = "glob"
    description = ("Find repository files by glob pattern; ** searches recursively. "
                   f"Skips dependency/cache directories and stops after {MAX_MATCHES} matches "
                   f"or {TIME_BUDGET:.0f}s, so prefer a directory prefix such as docs/**/*.md.")

    def __init__(self, workspace: Path, cancellation=None):
        super().__init__(workspace)
        self.cancellation = cancellation

    def schema(self):
        schema = super().schema()
        schema["function"]["parameters"].update({
            "properties": {"pattern": {"type": "string"}},
            "required": ["pattern"],
        })
        return schema

    def run(self, pattern: str) -> str:
        try:
            pattern = pattern.strip().removeprefix("./")
            if not pattern or Path(pattern).is_absolute() or ".." in Path(pattern).parts:
                return "Error: pattern must be relative to the repository"
            # Walk only below the literal prefix (e.g. docs/ in docs/**/*.md);
            # Path.glob walks everything and cannot be stopped midway.
            parts = pattern.split("/")
            fixed = []
            for part in parts[:-1]:
                if any(char in part for char in "*?["):
                    break
                fixed.append(part)
            root = self.safe_path("/".join(fixed)) if fixed else self.workspace
            if not root.is_dir():
                return "(no matches)"
            matcher = glob_regex(pattern)
            deadline = time.monotonic() + TIME_BUDGET
            matches, stopped = [], None
            for directory, dirnames, filenames in os.walk(root):
                dirnames[:] = sorted(name for name in dirnames if name not in SKIP_DIRS)
                base = Path(directory).relative_to(self.workspace).as_posix()
                for name in sorted(filenames):
                    relative = name if base == "." else f"{base}/{name}"
                    if matcher.match(relative):
                        matches.append(relative)
                if len(matches) >= MAX_MATCHES:
                    stopped = f"stopped after {MAX_MATCHES} matches"
                elif self.cancellation is not None and self.cancellation.is_set():
                    stopped = "cancelled"
                elif time.monotonic() > deadline:
                    stopped = f"stopped after {TIME_BUDGET:.0f}s"
                if stopped:
                    break
            shown = sorted(matches)[:MAX_MATCHES]
            if stopped:
                shown.append(f"... ({stopped}; results may be incomplete — narrow the pattern, e.g. docs/**/*.md)")
            return "\n".join(shown) if shown else "(no matches)"
        except (OSError, ValueError) as exc:
            return f"Error: {exc}"
