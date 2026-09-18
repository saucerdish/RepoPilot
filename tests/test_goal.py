import tempfile
import unittest
from pathlib import Path
from repopilot.runtime.goal import GoalController
from main import build_agent
from repopilot.runtime.host import AgentHost
from tests_helpers import ScriptedLLM


class GoalTests(unittest.TestCase):
    def test_stop_limit_returns_incomplete_and_keeps_goal_active(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = build_agent(Path(directory), llm=ScriptedLLM())
            host = AgentHost(agent)
            try:
                result = host.submit("/goal test evidence required")
                self.assertIn("Goal incomplete", result)
                self.assertTrue(agent.goal.state["active"])
                self.assertEqual(agent.goal.state["status"], "limited")
            finally:
                host.close()
    def test_defer_continue_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            pending = [True]
            verdict = {"ok": False, "impossible": False, "reason": "Test result missing"}
            goal = GoalController(Path(directory), lambda *args: verdict, lambda: pending[0])
            goal.set("tests exit 0")
            self.assertIsNone(goal.stop([]))
            self.assertEqual(goal.state["checks"], 0)
            pending[0] = False
            self.assertIn("not yet met", goal.stop([]))
            verdict["ok"] = True
            goal.stop([{"role": "tool", "content": "Exit code: 0"}])
            self.assertFalse(goal.state["active"])
            self.assertEqual(goal.state["status"], "completed")

    def test_failed_evaluator_preserves_active_goal(self):
        with tempfile.TemporaryDirectory() as directory:
            goal = GoalController(Path(directory), lambda *args: {"ok": "yes"})
            goal.set("finish")
            with self.assertRaises(RuntimeError):
                goal.stop([])
            restored = GoalController(Path(directory), None)
            self.assertTrue(restored.state["active"])
            self.assertEqual(restored.state["status"], "error")
            restored.limited("budget")
            self.assertTrue(restored.state["active"])
