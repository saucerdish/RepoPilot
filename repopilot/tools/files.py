"""Workspace-scoped file tools for repository tasks."""

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


class GlobTool(WorkspaceTool):
    name = "glob"
    description = "Find repository files by glob pattern; ** searches recursively."

    def schema(self):
        schema = super().schema()
        schema["function"]["parameters"].update({
            "properties": {"pattern": {"type": "string"}},
            "required": ["pattern"],
        })
        return schema

    def run(self, pattern: str) -> str:
        try:
            matches = sorted({
                str(path.relative_to(self.workspace))
                for path in self.workspace.glob(pattern)
                if path.is_file() and path.resolve().is_relative_to(self.workspace)
                and ".git" not in path.relative_to(self.workspace).parts
            })
            shown = matches[:200]
            if len(matches) > 200:
                shown.append("... (more matches omitted; narrow the pattern)")
            return "\n".join(shown) if shown else "(no matches)"
        except (OSError, ValueError) as exc:
            return f"Error: {exc}"
