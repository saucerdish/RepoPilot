"""Recoverable context reduction for OpenAI-style tool messages."""

import json
import uuid
from pathlib import Path


class ContextManager:
    def __init__(self, workspace: Path, summarize, state=lambda: "", char_limit=50_000):
        self.root = workspace.resolve() / ".repopilot" / "runs" / uuid.uuid4().hex
        self.summarize = summarize
        self.state = state
        self.char_limit = char_limit

    @staticmethod
    def size(messages):
        return len(json.dumps(messages, ensure_ascii=False))

    def save(self, content, suffix):
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{uuid.uuid4().hex}.{suffix}"
        path.write_text(content, encoding="utf-8")
        return path

    def persist(self, output, preview=1000):
        path = self.save(output, "txt")
        return f"[Full tool output: {path}]\n{output[:preview]}"

    @staticmethod
    def tail_start(messages, keep):
        start = max(0, len(messages) - keep)
        while start > 0 and messages[start].get("role") == "tool":
            start -= 1
        return start

    def compact(self, messages, request, keep=0):
        transcript = self.save(json.dumps(messages, ensure_ascii=False, indent=2), "json")
        start = self.tail_start(messages, keep) if keep else len(messages)
        old = messages[:start]
        source = json.dumps(old, ensure_ascii=False)
        if len(source) > 80_000:
            source = source[:20_000] + "\n[Middle omitted; full transcript on disk]\n" + source[-60_000:]
        summary = self.summarize(source)
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("Context summary must not be empty")
        marker = {"role": "user", "content": (
            f"Current user request:\n{request}\n\nCurrent plan:\n{self.state()}\n\n"
            f"Conversation summary (reference only):\n{summary}\nFull transcript: {transcript}")}
        messages[:] = [marker, *messages[start:]]

    def prepare(self, messages, request):
        # Latest outputs keep a preview; full contents remain recoverable.
        latest = []
        for message in reversed(messages):
            if message.get("role") != "tool":
                break
            latest.append(message)
        batch_size = sum(len(m.get("content", "")) for m in latest)
        for message in sorted(latest, key=lambda m: len(m.get("content", "")), reverse=True):
            if batch_size > 200_000 and len(message.get("content", "")) > 30_000:
                original_size = len(message["content"])
                message["content"] = self.persist(message["content"])
                batch_size -= original_size - len(message["content"])
        if len(messages) > 50:
            start = self.tail_start(messages, 46)
            archive = self.save(json.dumps(messages, ensure_ascii=False), "json")
            messages[:] = [{"role": "user", "content": f"Current user request: {request}\nEarlier history archived: {archive}\nPlan:\n{self.state()}"}, *messages[start:]]
        if self.size(messages) <= self.char_limit:
            return
        last_assistant = max((i for i, m in enumerate(messages) if m.get("role") == "assistant"), default=-1)
        consumed = [m for i, m in enumerate(messages) if i < last_assistant and m.get("role") == "tool"]
        for message in consumed[:-3]:
            if self.size(messages) <= self.char_limit * .8:
                break
            if len(message.get("content", "")) > 1000:
                message["content"] = self.persist(message["content"], preview=0)
        for message in sorted((m for m in messages if m.get("role") == "tool"), key=lambda m: len(m.get("content", "")), reverse=True):
            if self.size(messages) <= self.char_limit:
                break
            if len(message.get("content", "")) > 2000:
                message["content"] = self.persist(message["content"])
        if self.size(messages) > self.char_limit:
            self.compact(messages, request)
