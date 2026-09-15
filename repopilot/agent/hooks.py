"""Instance-scoped extension points for the agent lifecycle."""

from collections.abc import Callable


class HookManager:
    EVENTS = ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop")

    def __init__(self):
        self._callbacks: dict[str, list[Callable]] = {event: [] for event in self.EVENTS}

    def register(self, event: str, callback: Callable) -> None:
        if event not in self._callbacks:
            raise ValueError(f"Unknown hook event: {event}")
        self._callbacks[event].append(callback)

    def trigger(self, event: str, *args):
        if event not in self._callbacks:
            raise ValueError(f"Unknown hook event: {event}")
        first_result = None
        for callback in self._callbacks[event]:
            result = callback(*args)
            if event in ("PreToolUse", "Stop") and result is not None and first_result is None:
                first_result = result
                if event == "PreToolUse":
                    break
        return first_result
