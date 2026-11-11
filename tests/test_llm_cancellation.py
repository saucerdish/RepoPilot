import asyncio
import os
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from openai import AsyncOpenAI

from repopilot.llm.cancellation import RequestCancelled
from repopilot.llm.client import LLMClient
from repopilot.llm.telemetry import collect_metrics


class CancellationTests(unittest.TestCase):
    def make_llm(self, cancellation):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "MODEL_ID": "test-model"}):
            llm = LLMClient(Path("."), cancellation=cancellation)
        self.addCleanup(llm.client.close)
        return llm

    def test_cancel_in_flight_http_and_reuse_conversation(self):
        cancelled = threading.Event()
        entered = threading.Event()
        released = threading.Event()
        transports = []
        calls = []

        async def respond(request):
            calls.append(request)
            if len(calls) == 1:
                entered.set()
                try:
                    await asyncio.Event().wait()  # A provider that never replies.
                finally:
                    released.set()
            return httpx.Response(200, json={
                "id": "test-response", "object": "chat.completion", "created": 0,
                "model": "test-model", "choices": [{"index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "Next task completed."}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            })

        def client(**options):
            transport = httpx.AsyncClient(transport=httpx.MockTransport(respond))
            transports.append(transport)
            return AsyncOpenAI(http_client=transport, **options)

        def stop_when_started():
            entered.wait(2)
            cancelled.set()

        stopper = threading.Thread(target=stop_when_started)
        llm = self.make_llm(cancelled)
        stopper.start()
        try:
            with patch("repopilot.llm.client.AsyncOpenAI", side_effect=client), collect_metrics() as metrics:
                started = time.monotonic()
                with self.assertRaises(RequestCancelled):
                    llm.chat([{"role": "user", "content": "first"}])
                self.assertLess(time.monotonic() - started, 1.5)
                self.assertTrue(released.is_set())
                self.assertEqual(len(calls), 1)  # Cancellation must never retry.
                self.assertTrue(transports[0].is_closed)
                self.assertEqual(metrics.summary()["api_errors"], 0)
                cancelled.clear()
                response = llm.chat([{"role": "user", "content": "next"}])
                self.assertEqual(response.content, "Next task completed.")
                self.assertEqual(len(calls), 2)
                self.assertTrue(all(transport.is_closed for transport in transports))
        finally:
            cancelled.set()
            stopper.join(timeout=2)

    def test_stop_during_provider_retry_wait(self):
        retry_started = threading.Event()

        class Cancellation(threading.Event):
            def wait(self, timeout=None):
                retry_started.set()
                return super().wait(timeout)

        cancelled = Cancellation()
        calls = []

        async def unavailable(request):
            calls.append(request)
            return httpx.Response(503, json={"error": {"message": "try later"}})

        def client(**options):
            return AsyncOpenAI(http_client=httpx.AsyncClient(transport=httpx.MockTransport(unavailable)), **options)

        def stop_on_retry():
            retry_started.wait(2)
            cancelled.set()

        llm = self.make_llm(cancelled)
        stopper = threading.Thread(target=stop_on_retry)
        stopper.start()
        try:
            with patch("repopilot.llm.client.AsyncOpenAI", side_effect=client):
                with self.assertRaises(RequestCancelled):
                    llm.chat([{"role": "user", "content": "work"}])
            self.assertTrue(retry_started.is_set())
            self.assertEqual(len(calls), 1)
        finally:
            cancelled.set()
            stopper.join(timeout=2)

    def test_stopped_turn_does_not_open_another_request(self):
        cancelled = threading.Event()
        cancelled.set()
        llm = self.make_llm(cancelled)
        with patch("repopilot.llm.client.AsyncOpenAI") as client:
            with self.assertRaises(RequestCancelled):
                llm.chat([{"role": "user", "content": "work"}])
        client.assert_not_called()
