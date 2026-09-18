import tempfile
import unittest
from pathlib import Path
from repopilot.runtime.memory import MemoryStore


class MemoryTests(unittest.TestCase):
    def test_persistent_recall_and_temporary_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore(Path(directory))
            identifier = store.save("indent", "user", "indentation", "Prefer tabs")
            self.assertEqual(store.save("duplicate", "user", "style", "Prefer tabs"), identifier)
            self.assertIn("Prefer tabs", MemoryStore(Path(directory)).recall("indentation"))
            with self.assertRaises(ValueError):
                store.save("temp", "user", "temporary", "Do not write in this session")
            with self.assertRaises(ValueError):
                store.consolidate([{"name": "bad"}])
            self.assertEqual(len(store.records()), 1)
