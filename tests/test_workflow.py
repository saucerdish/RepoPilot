import tempfile
import unittest
from pathlib import Path
from repopilot.runtime.workflow import WorkflowRuntime, review_changes, validate


class WorkflowTests(unittest.TestCase):
    def test_partial_failure_resume_does_not_repeat_successful_step(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            broken = [True]
            def runner(prompt):
                calls.append(prompt)
                if "second" in prompt and broken[0]:
                    raise RuntimeError("temporary worker failure")
                return "result"
            runtime = WorkflowRuntime(Path(directory), runner)
            async def workflow(ctx, args):
                first = await ctx.agent("first", label="first")
                return await ctx.agent("second:" + first, label="second")
            runtime.register("resume", "resume", workflow, {"type": "object"})
            first = runtime.run("resume", {})
            self.assertEqual(first["status"], "failed")
            broken[0] = False
            second = runtime.run("resume", {}, first["run_id"])
            self.assertEqual(second["status"], "completed")
            self.assertEqual(calls.count("first"), 1)
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
