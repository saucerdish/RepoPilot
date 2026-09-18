import tempfile
import unittest
from pathlib import Path
from repopilot.runtime.workflow import WorkflowRuntime, review_changes, validate


class WorkflowTests(unittest.TestCase):
    def test_resume_reuses_semantic_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            runtime = WorkflowRuntime(Path(directory), lambda text: calls.append(text) or '{"findings":[]}')
            runtime.register("review", "review", review_changes, {"type": "object"})
            first = runtime.run("review", {"changes": "diff"})
            self.assertEqual(first["status"], "completed")
            self.assertEqual(len(calls), 4)
            second = runtime.run("review", {"changes": "diff"}, first["run_id"])
            self.assertEqual(second["status"], "completed")
            self.assertEqual(len(calls), 4)
            with self.assertRaises(ValueError):
                runtime.run("review", {"changes": "other"}, first["run_id"])

    def test_invalid_output_retries_then_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            runtime = WorkflowRuntime(Path(directory), lambda text: calls.append(text) or "invalid")
            async def workflow(ctx, args):
                return await ctx.agent("test", {"type": "object"})
            runtime.register("test", "test", workflow, {"type": "object"})
            result = runtime.run("test", {})
            self.assertEqual(result["status"], "failed")
            self.assertEqual(len(calls), 2)
            with self.assertRaises(ValueError):
                validate(True, {"type": "integer"})
