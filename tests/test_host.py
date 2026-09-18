import tempfile
import unittest
from pathlib import Path
from main import build_agent
from repopilot.runtime.host import AgentHost
from tests_helpers import ScriptedLLM


class HostTests(unittest.TestCase):
    def test_full_harness_and_async_permission_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = build_agent(Path(directory), llm=ScriptedLLM(), approval=lambda *args: False)
            host = AgentHost(agent)
            try:
                self.assertIn("spawn_teammate", agent.registry.tools)
                self.assertIn("connect_mcp", agent.registry.tools)
                self.assertEqual(host.submit("hello"), "done")
                self.assertIn("denied", agent.hooks.trigger("PreToolUse", "bash", {"command": "echo hello"}))
            finally:
                host.close()

    def test_schedule_ack_on_model_success_only(self):
        with tempfile.TemporaryDirectory() as directory:
            llm = ScriptedLLM()
            agent = build_agent(Path(directory), llm=llm)
            host = AgentHost(agent)
            try:
                called = []
                host.submit("scheduled", interactive=False, ack=lambda: called.append(True))
                self.assertEqual(called, [True])
                llm.error = RuntimeError("offline")
                with self.assertRaises(RuntimeError):
                    host.submit("scheduled", interactive=False, ack=lambda: called.append(False))
                self.assertEqual(called, [True])
            finally:
                host.close()
