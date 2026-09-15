class ToolRegistry:
    def __init__(self):
        self.tools = {}

    def register(self, tool):
        if not hasattr(tool, "name") or not tool.name:
            raise ValueError("tool must have a non-empty name")
        self.tools[tool.name] = tool

    def get(self, name):
        if not name:
            return None
        return self.tools.get(name)

    def schemas(self):
        return [tool.schema() for tool in self.tools.values()]