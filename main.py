from dotenv import load_dotenv
load_dotenv()

from repopilot.llm.client import LLMClient
from repopilot.agent.loop import Agent
from repopilot.tools.registry import ToolRegistry
from repopilot.tools.bash import BashTool

registry=ToolRegistry()
registry.register(BashTool())
agent=Agent(LLMClient(),registry)

print("Agent Loop")
print("Enter a question, press Enter to send. Type q to quit.\n")

history = []
while True:
    try:
    # \001/\002 tell Readline the ANSI escapes have zero display width.
        query = input("\001\033[36m\002s01 >> \001\033[0m\002")
    except (EOFError, KeyboardInterrupt):
        break
    if query.strip().lower() in ("q", "exit", ""):
        break
    history.append({"role": "user", "content": query})
    agent.run(history)
    response_content = history[-1]["content"]
    if response_content:
        print(response_content)
    print()
