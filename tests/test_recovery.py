import unittest
from types import SimpleNamespace
from repopilot.llm.recovery import resilient_chat


class RecoveryTests(unittest.TestCase):
    def test_connection_errors_have_bounded_retries(self):
        class APIConnectionError(Exception):
            pass
        calls = []
        def create(**params):
            calls.append(True)
            raise APIConnectionError("offline")
        with self.assertRaises(APIConnectionError):
            resilient_chat(create, {}, sleep=lambda _: None)
        self.assertEqual(len(calls), 3)
    def test_retry_and_truncation_do_not_execute_partial_output(self):
        requests = []
        def create(**params):
            requests.append(params)
            if len(requests) == 1:
                error = RuntimeError("busy")
                error.status_code = 429
                raise error
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason="length" if len(requests) == 2 else "stop")])
        resilient_chat(create, {"model": "test", "max_tokens": 4000}, sleep=lambda _: None)
        self.assertEqual(requests[-1]["max_tokens"], 8000)

    def test_unrelated_errors_are_not_retried(self):
        def create(**params):
            raise ValueError("bad configuration")
        with self.assertRaises(ValueError):
            resilient_chat(create, {})
