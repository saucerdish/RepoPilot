"""Model/tool loop with lifecycle hook extension points."""

import json

from .hooks import HookManager

class Agent:
    def __init__(self, llm, registry, hooks: HookManager | None = None, context=None, max_turns=100):
        self.llm = llm
        self.registry = registry
        self.hooks = hooks or HookManager()
        self.context = context
        self.max_turns = max_turns

    def run(self, messages: list, active_request=None):
        active_request = active_request or next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
        stop_continuations = 0
        rounds_since_todo = 0
        for _ in range(self.max_turns):
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
            messages.append(response.model_dump(exclude_none=True))
            tool_calls = response.tool_calls or []
            if not tool_calls:
                continuation = self.hooks.trigger("Stop", messages)
                if continuation and stop_continuations < 3:
                    messages.append({"role": "user", "content": str(continuation)})
                    stop_continuations += 1
                    continue
                return response.content

            results = []
            todo = self.registry.get("todo_write")
            version = todo.version if todo else 0
            for block in tool_calls:
                name = block.function.name
                try:
                    args = json.loads(block.function.arguments)
                    if not isinstance(args, dict):
                        raise ValueError("tool arguments must be an object")
                    blocked = self.hooks.trigger("PreToolUse", name, args)
                    if blocked is not None:
                        result = str(blocked)
                    else:
                        tool = self.registry.get(name)
                        if tool:
                            result = tool.run(**args)
                            self.hooks.trigger("PostToolUse", name, args, result)
                        else:
                            result = f"Error: unknown tool {name}"
                except (ValueError, TypeError) as exc:
                    result = f"Error: invalid tool call: {exc}"
                print(f"> {name}: {result[:200]}")
                results.append({"role": "tool", "tool_call_id": block.id, "content": result})
            messages.extend(results)
            compact = self.registry.get("compact")
            if compact and compact.requested and self.context:
                self.context.compact(messages, active_request)
                compact.requested = False
            if todo:
                rounds_since_todo = 0 if todo.version != version else rounds_since_todo + 1
                if rounds_since_todo >= 3:
                    messages.append({"role": "user", "content": f"Reminder: update your plan if progress changed.\n{todo.render()}"})
                    rounds_since_todo = 0
        return f"Agent stopped after {self.max_turns} turns without a final answer."
