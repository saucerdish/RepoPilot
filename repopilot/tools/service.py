import json

from .base import Tool


class ServiceTool(Tool):
    def __init__(self, name, description, handler, properties=None, required=None):
        self.name, self.description, self.handler = name, description, handler
        self.properties, self.required = properties or {}, required or []

    def schema(self):
        schema = super().schema()
        schema["function"]["parameters"].update({"properties": self.properties, "required": self.required, "additionalProperties": False})
        return schema

    def run(self, **kwargs):
        try:
            result = self.handler(**kwargs)
            return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            return f"Error: {type(exc).__name__}: {exc}"
