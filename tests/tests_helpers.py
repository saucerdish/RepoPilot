from types import SimpleNamespace


class ScriptedLLM:
    system_prompt = "test"
    error = None

    def chat(self, messages, tools):
        if self.error:
            raise self.error
        return SimpleNamespace(content="done", tool_calls=None, model_dump=lambda **_: {"role": "assistant", "content": "done"})

    def summarize(self, text):
        return "state summary"

    def decide(self, instruction, text):
        if "Judge whether" in instruction:
            return {"ok": False, "impossible": False, "reason": "Missing evidence"}
        return {"records": []}
