"""Model/tool loop with lifecycle hook extension points."""

import json

from .hooks import HookManager

class Agent:
    def __init__(self, llm, registry, hooks: HookManager | None = None):
        self.llm = llm
        self.registry = registry
        self.hooks = hooks or HookManager()

    def run(self, messages: list):
        stop_continuations = 0
        while True:
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
