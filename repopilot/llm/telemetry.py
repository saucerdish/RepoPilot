"""Opt-in metrics; never records prompts, response text or credentials."""

import threading
from contextlib import contextmanager
from contextvars import ContextVar


CURRENT_METRICS = ContextVar("repopilot_metrics", default=None)


class Metrics:
    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()

    def record(self, **values):
        with self.lock:
            self.calls.append(values)

    def summary(self):
        with self.lock:
            calls = list(self.calls)
        return {"api_calls": len(calls), "api_errors": sum(bool(c.get("error")) for c in calls),
                "prompt_tokens": sum(c.get("prompt_tokens", 0) for c in calls),
                "completion_tokens": sum(c.get("completion_tokens", 0) for c in calls),
                "total_tokens": sum(c.get("total_tokens", 0) for c in calls),
                "usage_reported_calls": sum(c.get("usage_reported", False) for c in calls), "calls": calls}


@contextmanager
def collect_metrics():
    metrics = Metrics()
    token = CURRENT_METRICS.set(metrics)
    try:
        yield metrics
    finally:
        CURRENT_METRICS.reset(token)
