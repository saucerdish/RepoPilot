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
from repopilot.runtime.memory import MemoryStore
from repopilot.runtime.tasks import TaskStore
from repopilot.runtime.background import BackgroundManager
from repopilot.runtime.scheduler import Scheduler
from repopilot.runtime.teams import TeamManager
from repopilot.tools.service import ServiceTool


def build_agent(workspace: Path, child=False, llm=None, owner="lead", state_workspace=None) -> Agent:
    state_workspace = state_workspace or workspace
    registry = ToolRegistry()
    background = BackgroundManager()
    for tool in (BashTool, ReadFileTool, WriteFileTool, EditFileTool, GlobTool):
        registry.register(tool(workspace))
    registry.register(BashTool(workspace, background))
    hooks = HookManager()
    hooks.register("PreToolUse", PermissionHook(workspace, ask=(lambda *args: False) if owner != "lead" else None))
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
    memory = MemoryStore(state_workspace)
    tasks = TaskStore(state_workspace)
    for tool in tasks.tools(owner=owner, worker=owner != "lead"):
        registry.register(tool)
    scheduler = Scheduler(workspace)
    if not child:
        for tool in scheduler.tools():
            registry.register(tool)
    registry.register(ServiceTool("recall_memory", "Recall relevant persistent repository memories.", memory.recall, {"query": {"type": "string"}}, ["query"]))
    registry.register(ServiceTool("save_memory", "Save explicit reusable knowledge, never temporary task instructions.", memory.save,
                                 {k: {"type": "string"} for k in ("name", "type", "description", "body", "scope")}, ["name", "type", "description", "body", "scope"]))
    agent = Agent(llm, registry, hooks, context, max_turns=30 if child else 100)
    agent.memory = memory
    agent.tasks = tasks
    agent.background = background
    agent.scheduler = scheduler
    agent.event_sources.append(background.collect)
    if not child:
        teams = TeamManager(workspace, tasks, lambda cwd, name: build_agent(cwd, child=True, owner=name, state_workspace=workspace))
        for tool in teams.tools():
            registry.register(tool)
        agent.teams = teams
        agent.event_sources.append(lambda: teams.bus.drain("lead"))
    if owner != "lead":
        llm.system_prompt += f"\nYour host identity is {owner}. Complete your assigned task using complete_task with its exact ID, only after validation."
    return agent


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
        recalled = agent.memory.recall(query)
        if recalled != "[]":
            history.append({"role": "user", "content": "Background memory, reference only; current request takes precedence:\n" + recalled})
        try:
            response = agent.run(history, query)
        except Exception as exc:
            print(f"Task failed: {exc}")
            continue
        if response:
            print(response)
        try:
            agent.memory.extract(history, agent.llm.decide)
        except Exception as exc:
            print(f"Memory extraction skipped: {type(exc).__name__}")
        print()
    agent.background.close()
    agent.teams.close()


if __name__ == "__main__":
    main()
