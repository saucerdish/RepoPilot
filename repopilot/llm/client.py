import os
from openai import OpenAI

class LLMClient:
    def __init__(self):
        self.client = OpenAI(
            api_key=os.environ["OPENAI_API_KEY"],
            base_url=os.getenv("OPENAI_BASE_URL"),
        )
        self.model = os.environ["MODEL_ID"]
        self.system_prompt = os.environ.get("SYSTEM_PROMPT", f"You are a coding agent at {os.getcwd()}. Use bash to solve tasks. Act, don't explain.")

    def chat(self,messages,tools=None):
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": self.system_prompt}, *messages],
            tools=tools,max_tokens=4000,
        )
        return response.choices[0].message