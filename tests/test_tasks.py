import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from repopilot.runtime.tasks import TaskStore


class TaskTests(unittest.TestCase):
    def test_dependencies_owner_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory))
            a, b = store.create("schema"), store.create("api")
            store.update(b["id"], [a["id"]])
            with self.assertRaises(ValueError):
                store.update(a["id"], [b["id"]])
            with self.assertRaises(ValueError):
                store.claim(b["id"])
            store.claim(a["id"], "worker")
            with self.assertRaises(ValueError):
                store.complete(a["id"], "other")
            store.complete(a["id"], "worker")
            self.assertEqual(TaskStore(Path(directory)).claim(b["id"])["status"], "in_progress")

    def test_only_one_concurrent_claim_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory))
            task = store.create("one")
            def claim(owner):
                try:
                    store.claim(task["id"], owner)
                    return True
                except ValueError:
                    return False
            with ThreadPoolExecutor(max_workers=2) as pool:
                self.assertEqual(sum(pool.map(claim, ["a", "b"])), 1)
