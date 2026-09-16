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
from repopilot.agent.context import ContextManager
from repopilot.tools.planning import TodoTool, CompactTool
from repopilot.tools.skills import SkillLoader
from repopilot.tools.task import TaskTool


def build_agent(workspace: Path, child=False, llm=None) -> Agent:
    registry = ToolRegistry()
    for tool in (BashTool, ReadFileTool, WriteFileTool, EditFileTool, GlobTool):
        registry.register(tool(workspace))
    hooks = HookManager()
    hooks.register("PreToolUse", PermissionHook(workspace))
    hooks.register("PostToolUse", lambda name, args, output: print(f"[hook] Large output from {name}") if len(output) > 100_000 else None)
    todo = TodoTool()
    skills = SkillLoader(workspace)
    registry.register(todo)
    registry.register(skills)
    registry.register(CompactTool())
    llm = llm or LLMClient(workspace)
    llm.system_prompt += ("\nPlan multi-step work with todo_write and keep progress current. "
                          "Load applicable skills before using their instructions.\nSkills:\n" + skills.catalog())
    if child:
        llm.system_prompt += "\nYou are a subagent. Complete only the delegated task and return a concise factual summary."
    else:
        registry.register(TaskTool(lambda: build_agent(workspace, child=True)))
    context = ContextManager(workspace, llm.summarize, todo.render)
    return Agent(llm, registry, hooks, context, max_turns=30 if child else 100)


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
        try:
            response = agent.run(history, query)
        except Exception as exc:
            print(f"Task failed: {exc}")
            continue
        if response:
            print(response)
        print()


if __name__ == "__main__":
    main()
