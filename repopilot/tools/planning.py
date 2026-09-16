from .base import Tool


class TodoTool(Tool):
    name = "todo_write"
    description = "Replace the current task plan; update progress during multi-step work."

    def __init__(self):
        self.items = []
        self.version = 0

    def schema(self):
        schema = super().schema()
        schema["function"]["parameters"].update({"properties": {"todos": {
            "type": "array", "maxItems": 20, "items": {"type": "object", "properties": {
                "content": {"type": "string", "minLength": 1},
                "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]}},
                "required": ["content", "status"]}}}, "required": ["todos"]})
        return schema

    def run(self, todos):
        if not isinstance(todos, list) or len(todos) > 20:
            return "Error: todos must be a list with at most 20 items"
        validated = []
        for item in todos:
            if not isinstance(item, dict) or not isinstance(item.get("content"), str) or not item["content"].strip():
                return "Error: each todo requires nonempty content"
            if item.get("status") not in ("pending", "in_progress", "completed"):
                return "Error: invalid todo status"
            validated.append({"content": item["content"].strip(), "status": item["status"]})
        if sum(item["status"] == "in_progress" for item in validated) > 1:
            return "Error: only one todo may be in_progress"
        self.items = validated
        self.version += 1
        return self.render()

    def render(self):
        markers = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}
        return "\n".join(f"{markers[item['status']]} {item['content']}" for item in self.items) or "No todos."


class CompactTool(Tool):
    name = "compact"
    description = "Request conversation summary after the current tool batch finishes."

    def __init__(self):
        self.requested = False

    def run(self):
        self.requested = True
        return "Compaction requested after this tool batch."
