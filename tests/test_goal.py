import tempfile
import unittest
from pathlib import Path
from repopilot.runtime.goal import GoalController


class GoalTests(unittest.TestCase):
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
