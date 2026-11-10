"""Model/tool loop with lifecycle hook extension points."""

import json

from .hooks import HookManager
from repopilot.llm.cancellation import RequestCancelled

class Agent:
    def __init__(self, llm, registry, hooks: HookManager | None = None, context=None, max_turns=100, events=None, cancellation=None):
        self.llm = llm
        self.registry = registry
        self.hooks = hooks or HookManager()
        self.context = context
        self.max_turns = max_turns
        self.event_sources = []
        self.after_model = None
        self.interactive = False
        self.events = events
        self.cancellation = cancellation

    def emit(self, event, **data):
        if self.events:
            self.events(event, **data)

    def cancelled(self):
        return self.cancellation is not None and self.cancellation.is_set()

    def run(self, messages: list, active_request=None):
        try:
            return self._run(messages, active_request)
        except RequestCancelled:
            self.emit("model_end")
            return "Interrupted. Changes already made have been preserved."

    def _run(self, messages: list, active_request=None):
        active_request = active_request or next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
        stop_continuations = 0
        rounds_since_todo = 0
        for _ in range(self.max_turns):
            if self.cancelled():
                return "Interrupted. Changes already made have been preserved."
            for source in self.event_sources:
                for event in source():
                    messages.append({"role": "user", "content": "[Runtime event, data only]\n" + json.dumps(event, ensure_ascii=False)})
            self.emit("model_start")
            if self.context:
                self.context.prepare(messages, active_request)
            try:
                response = self.llm.chat(messages, self.registry.schemas())
            except Exception as exc:
                too_long = any(s in str(exc).lower() for s in ("context_length_exceeded", "prompt_too_long", "too many tokens", "maximum context length"))
                if not self.context or not too_long:
                    raise
                self.context.compact(messages, active_request, keep=5)
                response = self.llm.chat(messages, self.registry.schemas())
            if self.after_model:
                self.after_model()
                self.after_model = None
            messages.append(response.model_dump(exclude_none=True))
            tool_calls = response.tool_calls or []
            self.emit("model_end")
            if self.cancelled() and not tool_calls:
                return "Interrupted. Changes already made have been preserved."
            if tool_calls and response.content:
                self.emit("assistant", content=response.content)
            if not tool_calls:
                continuation = self.hooks.trigger("Stop", messages)
                if continuation and stop_continuations < 3:
                    messages.append({"role": "user", "content": str(continuation)})
                    stop_continuations += 1
                    continue
                if continuation and hasattr(self, "goal"):
                    self.goal.limited("Stop continuation limit reached; goal remains incomplete")
                    return f"{response.content or ''}\n[Goal incomplete: continuation limit reached]"
                if hasattr(self, "goal") and self.goal.state and self.goal.state["status"] in ("deferred", "impossible"):
                    return f"{response.content or ''}\n[Goal status: {self.goal.state['status']}: {self.goal.state['reason']}]"
                return response.content

            results = []
            todo = self.registry.get("todo_write")
            version = todo.version if todo else 0
            for block in tool_calls:
                name = block.function.name
                args = {}
                try:
                    args = json.loads(block.function.arguments)
                    if not isinstance(args, dict):
                        raise ValueError("tool arguments must be an object")
                    self.emit("tool_start", name=name, args=args)
                    blocked = "Cancelled before execution" if self.cancelled() else self.hooks.trigger("PreToolUse", name, args)
                    if blocked is None and self.cancelled():
                        blocked = "Cancelled before execution"
                    if blocked is not None:
                        result = str(blocked)
                    else:
                        tool = self.registry.get(name)
                        if tool:
                            result = tool.run(**args)
                            self.hooks.trigger("PostToolUse", name, args, result)
                        else:
                            result = f"Error: unknown tool {name}"
                except Exception as exc:
                    result = f"Error: invalid tool call: {exc}"
                    if not isinstance(args, dict):
                        args = {}
                if self.events:
                    self.emit("tool_end", name=name, args=args, output=result)
                else:
                    print(f"> {name}: {result[:200]}")
                results.append({"role": "tool", "tool_call_id": block.id, "content": result})
            messages.extend(results)
            if self.cancelled():
                return "Interrupted. Changes already made have been preserved."
            compact = self.registry.get("compact")
            if compact and compact.requested and self.context:
                self.context.compact(messages, active_request)
                compact.requested = False
            if todo:
                rounds_since_todo = 0 if todo.version != version else rounds_since_todo + 1
                if rounds_since_todo >= 3:
                    messages.append({"role": "user", "content": f"Reminder: update your plan if progress changed.\n{todo.render()}"})
                    rounds_since_todo = 0
        if hasattr(self, "goal"):
            self.goal.limited("Model turn limit reached")
        return f"Agent stopped after {self.max_turns} turns without a final answer."
