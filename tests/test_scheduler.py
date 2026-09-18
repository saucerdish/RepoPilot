import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from repopilot.runtime.scheduler import Scheduler, parse_cron


class SchedulerTests(unittest.TestCase):
    def test_failed_delivery_survives_restart_and_ack(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Scheduler(Path(directory))
            job = store.schedule("*/5 * * * *", "run tests", recurring=False)
            moment = datetime(2026, 9, 18, 10, 5)
            self.assertEqual(len(store.poll(moment)), 1)
            restored = Scheduler(Path(directory))
            self.assertEqual(len(restored.poll(datetime(2026, 9, 18, 10, 6))), 1)
            restored.ack(job["id"])
            self.assertEqual(restored.poll(moment), [])

    def test_validation_and_same_minute_dedup(self):
        for invalid in ("* *", "60 * * * *", "*/0 * * * *", "0 0 0 * *"):
            with self.assertRaises(ValueError):
                parse_cron(invalid)
        with tempfile.TemporaryDirectory() as directory:
            store = Scheduler(Path(directory))
            job = store.schedule("* * * * *", "check")
            moment = datetime(2026, 9, 18, 10, 5)
            store.poll(moment)
            store.ack(job["id"])
            self.assertEqual(store.poll(moment), [])
