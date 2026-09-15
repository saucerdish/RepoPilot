import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from repopilot.agent.loop import Agent
from repopilot.agent.hooks import HookManager
from repopilot.tools.files import EditFileTool, GlobTool, ReadFileTool, WriteFileTool
from repopilot.tools.registry import ToolRegistry


class FileToolsTest(unittest.TestCase):
    def test_workspace_file_flow(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            write = WriteFileTool(workspace)
            read = ReadFileTool(workspace)
            edit = EditFileTool(workspace)
            glob = GlobTool(workspace)
            self.assertIn("Wrote", write.run("src/a.py", "hello\nworld\n"))
            self.assertEqual(read.run("src/a.py", limit=1), "hello\n... (1 more lines)")
            self.assertEqual(edit.run("src/a.py", "world", "repo"), "Edited src/a.py")
            self.assertEqual(read.run("src/a.py"), "hello\nrepo")
            self.assertEqual(glob.run("**/*.py"), str(Path("src/a.py")))

    def test_escape_and_ambiguous_edit_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            write = WriteFileTool(workspace)
            edit = EditFileTool(workspace)
            self.assertIn("Path escapes workspace", write.run("../outside.txt", "x"))
            write.run("a.txt", "same same")
            self.assertIn("found 2", edit.run("a.txt", "same", "new"))
            self.assertEqual((workspace / "a.txt").read_text(), "same same")


class AgentDispatchTest(unittest.TestCase):
    def test_multiple_calls_keep_order_and_invalid_call_returns_result(self):
        class FakeLLM:
            def __init__(self):
                self.calls = 0

            def chat(self, messages, tools):
                self.calls += 1
                if self.calls > 1:
                    return SimpleNamespace(content="done", tool_calls=None, model_dump=lambda **_: {"role": "assistant", "content": "done"})
                calls = [
                    SimpleNamespace(id="1", function=SimpleNamespace(name="write_file", arguments=json.dumps({"path": "a.txt", "content": "ok"}))),
                    SimpleNamespace(id="2", function=SimpleNamespace(name="read_file", arguments=json.dumps({"path": "a.txt"}))),
                    SimpleNamespace(id="3", function=SimpleNamespace(name="missing", arguments="{}")),
                ]
                return SimpleNamespace(content=None, tool_calls=calls, model_dump=lambda **_: {"role": "assistant", "tool_calls": []})

        with tempfile.TemporaryDirectory() as directory:
            registry = ToolRegistry()
            registry.register(WriteFileTool(Path(directory)))
            registry.register(ReadFileTool(Path(directory)))
            hooks = HookManager()
            completed = []
            hooks.register("PostToolUse", lambda name, args, output: completed.append(name))
            messages = [{"role": "user", "content": "task"}]
            self.assertEqual(Agent(FakeLLM(), registry, hooks).run(messages), "done")
            self.assertEqual([message["tool_call_id"] for message in messages if message["role"] == "tool"], ["1", "2", "3"])
            self.assertEqual(messages[3]["content"], "ok")
            self.assertIn("unknown tool", messages[4]["content"])
            self.assertEqual(completed, ["write_file", "read_file"])


if __name__ == "__main__":
    unittest.main()
