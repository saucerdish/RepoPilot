import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from repopilot.runtime.teams import MessageBus, TeamManager
from repopilot.runtime.tasks import TaskStore
from main import build_agent
from tests_helpers import ScriptedLLM
from types import SimpleNamespace


class TeamTests(unittest.TestCase):
    def test_persistent_worker_completes_owned_task_and_reports_idle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tasks = TaskStore(root)
            task = tasks.create("read repository")
            done = threading.Event()
            class WorkerLLM(ScriptedLLM):
                calls = 0
                def chat(self, messages, schemas):
                    self.calls += 1
                    if self.calls == 1:
                        import json
                        arguments = json.dumps({"task_id": task["id"]})
                        block = SimpleNamespace(id="complete", function=SimpleNamespace(name="complete_task", arguments=arguments))
                        return SimpleNamespace(content=None, tool_calls=[block], model_dump=lambda **_: {"role": "assistant", "tool_calls": [{"id": "complete", "type": "function", "function": {"name": "complete_task", "arguments": arguments}}]})
                    return super().chat(messages, schemas)
            team = TeamManager(root, tasks, lambda cwd, name: build_agent(cwd, child=True, owner=name, state_workspace=root, llm=WorkerLLM()))
            send = team.bus.send
            def notify(*args, **kwargs):
                result = send(*args, **kwargs)
                if result["type"] == "idle_notification":
                    done.set()
                return result
            team.bus.send = notify
            try:
                team.spawn("alice", task["id"])
                self.assertTrue(done.wait(5))
                events = team.bus.drain("lead")
                self.assertEqual([event["type"] for event in events], ["result", "idle_notification"])
                self.assertEqual(tasks.get(task["id"])["status"], "completed")
            finally:
                team.close()
    def test_bus_drain_is_exactly_once(self):
        with tempfile.TemporaryDirectory() as directory:
            bus = MessageBus(Path(directory))
            bus.send("a", "lead", "done", "result")
            self.assertEqual(bus.drain("lead")[0]["type"], "result")
            self.assertEqual(bus.drain("lead"), [])

    def test_stale_plan_cannot_approve_new_assignment(self):
        with tempfile.TemporaryDirectory() as directory:
            tasks = TaskStore(Path(directory))
            team = TeamManager(Path(directory), tasks, None)
            task = tasks.create("test")
            state = {"name": "alice", "task": task, "version": 1, "gate": "required", "stop": threading.Event(), "wake": threading.Event()}
            team.members["alice"] = state
            request = team.submit_plan("alice", "plan")
            self.assertIn("Blocked", team._gate(state, "write_file", {}))
            state["version"] = 2
            with self.assertRaises(ValueError):
                team.review_plan(request["request_id"], True)
            state["version"] = 1
            team.review_plan(request["request_id"], True)
            self.assertIsNone(team._gate(state, "write_file", {}))

    def test_worktree_is_bound_and_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.run(["git", *args], cwd=root, capture_output=True, check=True)
            git("init")
            (root / "file.txt").write_text("base")
            git("add", "file.txt")
            git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "base")
            store = TaskStore(root)
            team = TeamManager(root, store, None)
            task = store.create("change")
            checkout = Path(team.create_worktree("change", task["id"])["cwd"])
            (checkout / "file.txt").write_text("changed")
            self.assertEqual((root / "file.txt").read_text(), "base")
            self.assertEqual(store.get(task["id"])["cwd"], str(checkout))
