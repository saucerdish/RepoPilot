"""Run RepoPilot against a selected Git repository."""

import argparse
from pathlib import Path

from dotenv import load_dotenv

from repopilot.agent.loop import Agent
from repopilot.agent.hooks import HookManager
from repopilot.agent.permission import PermissionHook
from repopilot.llm.client import LLMClient
from repopilot.tools.bash import BashTool
from repopilot.tools.files import EditFileTool, GlobTool, ReadFileTool, WriteFileTool
from repopilot.tools.registry import ToolRegistry


def build_agent(workspace: Path) -> Agent:
    registry = ToolRegistry()
    for tool in (BashTool, ReadFileTool, WriteFileTool, EditFileTool, GlobTool):
        registry.register(tool(workspace))
    hooks = HookManager()
    hooks.register("PreToolUse", PermissionHook(workspace))
    hooks.register("PostToolUse", lambda name, args, output: print(f"[hook] Large output from {name}") if len(output) > 100_000 else None)
    return Agent(LLMClient(workspace), registry, hooks)


def main() -> None:
    parser = argparse.ArgumentParser(description="Software engineering agent for a Git repository")
    parser.add_argument("repository", nargs="?", default=".", help="path to the target Git repository")
    args = parser.parse_args()
    workspace = Path(args.repository).resolve()
    if not workspace.is_dir() or not (workspace / ".git").exists():
        parser.error(f"not a Git repository: {workspace}")

    load_dotenv()
    agent = build_agent(workspace)
    print(f"RepoPilot: {workspace}\nEnter a task. Type q to quit.\n")
    history = []
    while True:
        try:
            query = input("repopilot >> ")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        agent.hooks.trigger("UserPromptSubmit", query)
        history.append({"role": "user", "content": query})
        response = agent.run(history)
        if response:
            print(response)
        print()


if __name__ == "__main__":
    main()
