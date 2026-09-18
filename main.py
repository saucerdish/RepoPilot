"""Run RepoPilot against a selected Git repository."""

import argparse
from pathlib import Path


from repopilot.agent.loop import Agent
from repopilot.agent.hooks import HookManager
from repopilot.agent.permission import PermissionHook
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
from repopilot.runtime.mcp import MCPManager
from repopilot.tools.service import ServiceTool
from repopilot.runtime.host import AgentHost


def build_agent(workspace: Path, child=False, llm=None, owner="lead", state_workspace=None, mcp_servers=None, approval=None, allow_commands=()) -> Agent:
    state_workspace = state_workspace or workspace
    registry = ToolRegistry()
    background = BackgroundManager()
    for tool in (ReadFileTool, WriteFileTool, EditFileTool, GlobTool):
        registry.register(tool(workspace))
    registry.register(BashTool(workspace, background))
    hooks = HookManager()
    holder = {}
    def ask(name, args, reason):
        if approval:
            return approval(name, args, reason)
        if owner == "lead" and holder.get("agent") and holder["agent"].interactive:
            return PermissionHook.ask_user(name, args, reason)
        return False
    hooks.register("PreToolUse", PermissionHook(workspace, ask=ask, confirm_shell=True, allow_commands=allow_commands))
    hooks.register("PostToolUse", lambda name, args, output: print(f"[hook] Large output from {name}") if len(output) > 100_000 else None)
    todo = TodoTool()
    skills = SkillLoader(workspace)
    registry.register(todo)
    registry.register(skills)
    registry.register(CompactTool())
    if llm is None:
        from repopilot.llm.client import LLMClient
        llm = LLMClient(workspace)
    llm.system_prompt += ("\nPlan multi-step work with todo_write and keep progress current. "
                          "Load applicable skills before using their instructions.\nSkills:\n" + skills.catalog())
    if child:
        llm.system_prompt += "\nYou are a subagent. Complete only the delegated task and return a concise factual summary."
    else:
        registry.register(TaskTool(lambda: build_agent(workspace, child=True, owner="subagent", state_workspace=state_workspace, approval=ask, allow_commands=allow_commands)))
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
    holder["agent"] = agent
    agent.memory = memory
    agent.tasks = tasks
    agent.background = background
    agent.scheduler = scheduler
    agent.event_sources.append(background.collect)
    mcp = MCPManager(workspace, registry, mcp_servers)
    if not child:
        registry.register(ServiceTool("connect_mcp", "Connect a host-configured MCP server and discover tools.", mcp.connect, {"name": {"type": "string"}}, ["name"]))
    hooks.register("PreToolUse", mcp.permission(ask))
    agent.mcp = mcp
    if not child:
        teams = TeamManager(workspace, tasks, lambda cwd, name: build_agent(cwd, child=True, owner=name, state_workspace=workspace, allow_commands=allow_commands))
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
    parser.add_argument("--mcp-config", type=Path, help="host-approved JSON server configuration")
    parser.add_argument("--task", help="run one task without interactive approvals")
    parser.add_argument("--allow-command", action="append", default=[], help="explicitly authorize one exact shell command, including async turns")
    args = parser.parse_args()
    workspace = Path(args.repository).resolve()
    if not workspace.is_dir() or not (workspace / ".git").exists():
        parser.error(f"not a Git repository: {workspace}")

    from dotenv import load_dotenv
    load_dotenv()
    import json
    mcp_servers = json.loads(args.mcp_config.read_text(encoding="utf-8")) if args.mcp_config else {}
    import queue
    import threading
    inputs = queue.Queue()
    host_ref = {}
    def approve(name, arguments, reason):
        if args.task or not host_ref["host"].agent.interactive:
            return False
        print(f"Permission: {reason}\n{name}: {arguments}\nAllow? [y/N]", flush=True)
        answer = inputs.get()
        return isinstance(answer, str) and answer.strip().lower() in ("y", "yes")
    agent = build_agent(workspace, mcp_servers=mcp_servers, approval=approve, allow_commands=args.allow_command)
    host = AgentHost(agent)
    host_ref["host"] = host
    try:
        if args.task:
            print(host.submit(args.task, interactive=True))
            return
        def read_input():
            while True:
                try:
                    inputs.put(input())
                except (EOFError, KeyboardInterrupt):
                    inputs.put(None)
                    return
        threading.Thread(target=read_input, daemon=True).start()
        print(f"RepoPilot: {workspace}\nEnter a task. Type q to quit.", flush=True)
        while True:
            try:
                query = inputs.get(timeout=.5)
            except queue.Empty:
                for output in host.poll():
                    if output:
                        print(output, flush=True)
                continue
            if query is None or query.strip().lower() in ("q", "exit"):
                break
            if not query.strip():
                continue
            try:
                print(host.submit(query), flush=True)
            except Exception as exc:
                print(f"Task failed: {type(exc).__name__}: {exc}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        host.close()


if __name__ == "__main__":
    main()
