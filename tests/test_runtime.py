import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from repopilot.agent.context import ContextManager
from repopilot.agent.loop import Agent
from repopilot.tools.planning import TodoTool, CompactTool
from repopilot.tools.skills import SkillLoader
from repopilot.tools.task import TaskTool
from repopilot.tools.registry import ToolRegistry


def response(calls=None, content="done"):
    return SimpleNamespace(content=content, tool_calls=calls, model_dump=lambda **_: {
        "role": "assistant", "content": content,
        **({"tool_calls": [{"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}} for c in calls]} if calls else {})})


class RuntimeTests(unittest.TestCase):
    def test_invalid_plan_does_not_replace_existing_state(self):
        todo = TodoTool()
        todo.run([{"content": "inspect", "status": "pending"}])
        self.assertIn("Error", todo.run([{"content": "a", "status": "in_progress"}, {"content": "b", "status": "in_progress"}]))
        self.assertEqual(todo.items[0]["content"], "inspect")
        self.assertEqual(todo.version, 1)

    def test_skills_catalog_and_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "skills" / "review" / "SKILL.md"
            path.parent.mkdir(parents=True)
            path.write_text("---\nname: review\ndescription: Review code\n---\nFull instructions", encoding="utf-8")
            loader = SkillLoader(root)
            self.assertNotIn("Full instructions", loader.catalog())
            self.assertIn("Full instructions", loader.run("review"))
            self.assertIn("Error", loader.run("../review"))

    def test_task_uses_fresh_history(self):
        histories = []
        class Child:
            def run(self, messages, request):
                histories.append(messages)
                return "summary"
        task = TaskTool(Child)
        self.assertEqual(task.run("inspect"), "summary")
        task.run("test")
        self.assertIsNot(histories[0], histories[1])
        self.assertEqual(histories[0], [{"role": "user", "content": "inspect"}])

    def test_archive_preserves_multi_call_pairs_and_full_output(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = ContextManager(Path(directory), lambda _: "summary", char_limit=50_000)
            messages = [{"role": "user", "content": "request"}]
            for i in range(20):
                messages.extend([{"role": "assistant", "tool_calls": [{"id": f"a{i}"}, {"id": f"b{i}"}]},
                                 {"role": "tool", "tool_call_id": f"a{i}", "content": "x" * 31000},
                                 {"role": "tool", "tool_call_id": f"b{i}", "content": "ok"}])
            manager.prepare(messages, "request")
            self.assertNotEqual(messages[1]["role"], "tool")
            self.assertTrue(any(p.read_text() == "x" * 31000 for p in manager.root.glob("*.txt")))
            for i, message in enumerate(messages):
                if message["role"] == "assistant":
                    self.assertEqual([m["tool_call_id"] for m in messages[i+1:i+3]], [c["id"] for c in message["tool_calls"]])

    def test_compact_waits_for_entire_batch(self):
        class FakeLLM:
            def __init__(self):
                self.calls = 0
            def chat(self, messages, schemas):
                self.calls += 1
                if self.calls == 1:
                    calls = [SimpleNamespace(id="1", function=SimpleNamespace(name="compact", arguments="{}")),
                             SimpleNamespace(id="2", function=SimpleNamespace(name="missing", arguments="{}"))]
                    return response(calls)
                return response()
        snapshots = []
        class Context:
            def prepare(self, messages, request):
                pass
            def compact(self, messages, request):
                snapshots.append(list(messages))
        registry = ToolRegistry()
        registry.register(CompactTool())
        Agent(FakeLLM(), registry, context=Context()).run([{"role": "user", "content": "task"}])
        self.assertEqual([m["tool_call_id"] for m in snapshots[0][-2:]], ["1", "2"])

    def test_context_error_retries_only_once(self):
        class LLM:
            def __init__(self):
                self.calls = 0
            def chat(self, *args):
                self.calls += 1
                raise ValueError("context_length_exceeded")
        class Context:
            def prepare(self, *args):
                pass
            def compact(self, *args, **kwargs):
                pass
        llm = LLM()
        with self.assertRaises(ValueError):
            Agent(llm, ToolRegistry(), context=Context()).run([{"role": "user", "content": "task"}])
        self.assertEqual(llm.calls, 2)


if __name__ == "__main__":
    unittest.main()
