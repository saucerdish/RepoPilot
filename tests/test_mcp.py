import sys
import tempfile
import unittest
from pathlib import Path
from repopilot.runtime.mcp import MCPManager, StdioClient
from repopilot.tools.registry import ToolRegistry


class MCPTests(unittest.TestCase):
    def test_real_stdio_initialize_discovery_and_call(self):
        script = '''import sys,json
for line in sys.stdin:
 m=json.loads(line)
 if "id" not in m: continue
 if m["method"]=="initialize": r={"protocolVersion":"2025-11-25","capabilities":{"tools":{}},"serverInfo":{"name":"test","version":"1"}}
 elif m["method"]=="tools/list": r={"tools":[{"name":"echo","inputSchema":{"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}}]}
 else: r={"content":[{"type":"text","text":m["params"]["arguments"]["text"]}]}
 print(json.dumps({"jsonrpc":"2.0","id":m["id"],"result":r}),flush=True)
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "server.py"
            path.write_text(script)
            registry = ToolRegistry()
            manager = MCPManager(root, registry, {"docs": {"command": [sys.executable, str(path)], "allow_tools": ["echo"]}})
            try:
                manager.connect("docs")
                self.assertIn("hello", registry.get("mcp__docs__echo").run(text="hello"))
                self.assertIsNone(manager.permission(lambda *args: False)("mcp__docs__echo", {}))
                self.assertIn("denied", manager.permission(lambda *args: False)("mcp__unknown__write", {}))
            finally:
                manager.close()

    def test_collision_rolls_back_tool_discovery(self):
        class Client:
            def __init__(self, *args): pass
            def list_tools(self):
                return [{"name": "a.b"}, {"name": "a_b"}]
            def close(self): pass
        manager = MCPManager(Path.cwd(), ToolRegistry(), {"docs": {"command": ["unused"]}}, Client)
        with self.assertRaises(ValueError):
            manager.connect("docs")
        self.assertEqual(manager.registry.schemas(), [])
