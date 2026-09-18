import tempfile
import unittest
from pathlib import Path
from repopilot.runtime.background import BackgroundManager


class BackgroundTests(unittest.TestCase):
    def test_nonzero_status_and_exactly_once_notification(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = BackgroundManager()
            try:
                runtime.start("exit 7", Path(directory))
                event = runtime.results.get(timeout=10)
                self.assertEqual(event["status"], "failed")
                self.assertIn("Exit code: 7", event["output"])
                self.assertEqual(runtime.collect(), [])
            finally:
                runtime.close()

    def test_sync_includes_command_and_exit_code(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = BackgroundManager()
            try:
                self.assertIn("Exit code: 0", runtime.execute("echo hello", Path(directory)))
            finally:
                runtime.close()
