import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from repopilot.agent.hooks import HookManager
from repopilot.agent.loop import Agent
from repopilot.agent.permission import PermissionHook
from repopilot.tools.registry import ToolRegistry


class HookManagerTest(unittest.TestCase):
    def test_instances_are_isolated_and_pre_hook_short_circuits(self):
        one, two = HookManager(), HookManager()
        events = []
        one.register("PreToolUse", lambda name, args: "blocked")
        one.register("PreToolUse", lambda name, args: events.append("second"))
        self.assertEqual(one.trigger("PreToolUse", "bash", {}), "blocked")
        self.assertEqual(events, [])
        self.assertIsNone(two.trigger("PreToolUse", "bash", {}))
        with self.assertRaises(ValueError):
            one.register("Unknown", lambda: None)

    def test_permission_decisions(self):
        with tempfile.TemporaryDirectory() as directory:
            asked = []
            permission = PermissionHook(Path(directory), ask=lambda *args: asked.append(args) or False)
            self.assertIsNone(permission("bash", {"command": "echo model"}))
            self.assertIsNone(permission("read_file", {"path": "a.py"}))
            self.assertIn("outside repository", permission("write_file", {"path": "../outside.py"}))
            self.assertIn("blocked command", permission("bash", {"command": "RM -RF /"}))
            self.assertIn("by user", permission("bash", {"command": "del a.txt"}))
            self.assertEqual(len(asked), 1)
            permission.ask = lambda *args: True
            self.assertIsNone(permission("bash", {"command": "DEL a.txt"}))

    def test_pre_and_post_hooks_in_agent(self):
        class FakeLLM:
            def __init__(self):
                self.calls = 0

            def chat(self, messages, schemas):
                self.calls += 1
                if self.calls > 1:
                    return SimpleNamespace(content="done", tool_calls=None, model_dump=lambda **_: {"role": "assistant", "content": "done"})
                block = SimpleNamespace(id="1", function=SimpleNamespace(name="bash", arguments='{"command": "del a.txt"}'))
                return SimpleNamespace(content=None, tool_calls=[block], model_dump=lambda **_: {"role": "assistant", "tool_calls": []})

        hooks = HookManager()
        post_calls = []
        hooks.register("PreToolUse", lambda name, args: "Permission denied")
        hooks.register("PostToolUse", lambda *args: post_calls.append(args))
        messages = [{"role": "user", "content": "task"}]
        self.assertEqual(Agent(FakeLLM(), ToolRegistry(), hooks).run(messages), "done")
        self.assertEqual(messages[2]["content"], "Permission denied")
        self.assertEqual(post_calls, [])

    def test_stop_hook_can_continue_once(self):
        class FakeLLM:
            def chat(self, messages, schemas):
                return SimpleNamespace(content="done", tool_calls=None, model_dump=lambda **_: {"role": "assistant", "content": "done"})

        hooks = HookManager()
        count = [0]

        def continue_once(messages):
            count[0] += 1
            return "check again" if count[0] == 1 else None

        hooks.register("Stop", continue_once)
        messages = [{"role": "user", "content": "task"}]
        Agent(FakeLLM(), ToolRegistry(), hooks).run(messages)
        self.assertEqual(messages[2], {"role": "user", "content": "check again"})
        self.assertEqual(count[0], 2)


if __name__ == "__main__":
    unittest.main()
