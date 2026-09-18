import contextlib
import io
import unittest
from repopilot.evaluation import evaluate


class EvaluationTests(unittest.TestCase):
    def test_scripted_demo_is_independently_verified(self):
        with contextlib.redirect_stdout(io.StringIO()):
            report = evaluate("demo")
        self.assertEqual(report["passed"], 3)
        self.assertEqual(report["total"], 3)
        self.assertIn("not model performance", report["description"])
        self.assertTrue(all(case["initial_exit"] != 0 and case["final_exit"] == 0 and case["tests_unchanged"] for case in report["cases"]))
