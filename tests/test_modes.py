import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from repopilot.agent.permission import ApprovalMode
from repopilot.factory import build_agent
from tests_helpers import ScriptedLLM


class ApprovalModeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.mode = ApprovalMode()
        self.cancelled = threading.Event()
        self.asked = []
        self.agent = build_agent(self.root, llm=ScriptedLLM(), mode=self.mode,
                                 approval=lambda *args: self.asked.append(args) or False,
                                 cancellation=self.cancelled)
        self.close_on_cleanup(self.agent)

    def close_on_cleanup(self, agent):
        self.addCleanup(agent.mcp.close)
        self.addCleanup(agent.background.close)
        if hasattr(agent, "teams"):
            self.addCleanup(agent.teams.close)

    def test_mode_controls_shell_team_worktree_and_mcp_approvals(self):
        operations = (("bash", {"command": "echo fixture"}),
                      ("spawn_teammate", {}), ("create_worktree", {}),
                      ("connect_mcp", {}), ("mcp__fixture__tool", {}))
        for value in ("manual", "auto", "manual"):
            self.mode.set(value)
            before = len(self.asked)
            for name, args in operations:
                with self.subTest(mode=value, tool=name):
                    result = self.agent.hooks.trigger("PreToolUse", name, args)
                    if value == "auto":
                        self.assertIsNone(result)
                    else:
                        self.assertIn("Permission denied", result)
            self.assertEqual(len(self.asked) - before, 0 if value == "auto" else len(operations))

    def test_auto_preserves_repository_and_command_boundaries(self):
        self.mode.set("auto")
        hook = self.agent.hooks.trigger
        self.assertIn("blocked command", hook("PreToolUse", "bash", {"command": "sudo true"}))
        self.assertIn("outside repository", hook("PreToolUse", "write_file", {"path": "../outside.py"}))
        self.assertEqual(self.asked, [])

    def test_children_share_mode_changes(self):
        with patch("repopilot.llm.client.LLMClient", side_effect=lambda *a, **kw: ScriptedLLM()):
            children = (self.agent.registry.get("task").factory(),
                        self.agent.teams.factory(self.root, "fixture-worker"))
        for child in children:
            self.close_on_cleanup(child)
            self.assertIs(child.approval_mode, self.mode)
            for value in ("auto", "manual"):
                self.mode.set(value)
                result = child.hooks.trigger("PreToolUse", "bash", {"command": "echo fixture"})
                self.assertIsNone(result) if value == "auto" else self.assertIn("Permission denied", result)

    def test_cancellation_still_blocks_auto_approval(self):
        self.mode.set("auto")
        self.cancelled.set()
        self.assertIn("Permission denied", self.agent.hooks.trigger("PreToolUse", "bash", {"command": "echo fixture"}))

    def test_invalid_mode_is_rejected_without_changing_policy(self):
        with self.assertRaisesRegex(ValueError, "manual or auto"):
            self.mode.set("invalid")
        self.assertEqual(self.mode.value, "manual")
