import os
from pathlib import Path
from openai import OpenAI

class LLMClient:
    def __init__(self, workspace: Path):
        self.client = OpenAI(
            api_key=os.environ["OPENAI_API_KEY"],
            base_url=os.getenv("OPENAI_BASE_URL"),
        )
        self.model = os.environ["MODEL_ID"]
        self.system_prompt = os.environ.get("SYSTEM_PROMPT", f"You are a software engineering agent working in the Git repository at {workspace.resolve()}. Inspect relevant files, make targeted changes, run tests, and report results. Use the dedicated file tools for file operations.")

    def chat(self,messages,tools=None):
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": self.system_prompt}, *messages],
            tools=tools,max_tokens=4000,
        )
        return response.choices[0].message
