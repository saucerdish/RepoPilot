from .base import Tool


class TaskTool(Tool):
    name = "task"
    description = "Delegate a bounded task with fresh conversation history; only final text returns."

    def __init__(self, factory):
        self.factory = factory

    def schema(self):
        schema = super().schema()
        schema["function"]["parameters"].update({"properties": {"prompt": {"type": "string", "minLength": 1}}, "required": ["prompt"]})
        return schema

    def run(self, prompt: str):
        if not prompt.strip():
            return "Error: task prompt must not be empty"
        agent = self.factory()
        try:
            return agent.run([{"role": "user", "content": prompt}], prompt) or "(no summary)"
        finally:
            if hasattr(agent, "background"):
                agent.background.close()
