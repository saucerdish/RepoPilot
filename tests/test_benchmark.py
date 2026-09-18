import tempfile
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
from repopilot.benchmark import prepare, validate_public, grade, classify, execute_worker
from repopilot.benchmark_cases import CASES
from repopilot.llm.telemetry import collect_metrics, CURRENT_METRICS


class BenchmarkTests(unittest.TestCase):
    def test_supervisor_records_normal_worker_exit_without_results(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            case_dir = Path(directory)
            result_path = case_dir / "result.json"
            process = MagicMock()
            with patch("repopilot.benchmark.subprocess.Popen", return_value=process):
                execute_worker(case_dir, case_dir, {"name": "fixture"}, result_path, 1, 1, {}, {})
            result = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertFalse(result["timed_out"])
            self.assertEqual(result["error"], "Worker exited without results")

    def test_normal_worker_failure_with_null_error_is_classified(self):
        result = dict(passed=False, error=None, scope_ok=True, functional_pass=True)
        self.assertEqual(classify(result), "goal_incomplete")
        result["functional_pass"] = False
        self.assertEqual(classify(result), "acceptance_failure")

    def test_all_fixtures_start_failing_and_graders_run(self):
        with tempfile.TemporaryDirectory() as directory:
            for case in CASES:
                with self.subTest(case=case["name"]):
                    root = Path(directory) / case["name"]
                    prepare(root, case)
                    self.assertNotEqual(validate_public(root).returncode, 0)
                    acceptance = grade(root, case, Path(directory) / f"{case['name']}.py")
                    self.assertNotEqual(acceptance.returncode, 0)
                    self.assertIn("Ran ", acceptance.stderr)
                    self.assertNotIn("SyntaxError", acceptance.stderr)

    def test_telemetry_is_opt_in_and_counts_reported_usage(self):
        self.assertIsNone(CURRENT_METRICS.get())
        with collect_metrics() as metrics:
            metrics.record(purpose="chat", error=None, usage_reported=True, total_tokens=10)
            metrics.record(purpose="chat", error="APIConnectionError")
            self.assertEqual(metrics.summary()["api_calls"], 2)
            self.assertEqual(metrics.summary()["api_errors"], 1)
            self.assertEqual(metrics.summary()["total_tokens"], 10)
            self.assertEqual(metrics.summary()["usage_reported_calls"], 1)
        self.assertIsNone(CURRENT_METRICS.get())
