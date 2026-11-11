"""Construct the agent harness independently of the terminal client."""

from pathlib import Path


from repopilot.agent.loop import Agent
from repopilot.agent.hooks import HookManager
from repopilot.agent.permission import ApprovalMode, PermissionHook
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
from repopilot.runtime.workflow import WorkflowRuntime, review_changes
from repopilot.runtime.goal import GoalController


def build_agent(workspace: Path, child=False, llm=None, owner="lead", state_workspace=None, mcp_servers=None, approval=None, allow_commands=(), events=None, cancellation=None, mode="manual") -> Agent:
    mode = mode if isinstance(mode, ApprovalMode) else ApprovalMode(mode)
    state_workspace = state_workspace or workspace
    registry = ToolRegistry()
    background = BackgroundManager()
    for tool in (ReadFileTool, WriteFileTool, EditFileTool):
        registry.register(tool(workspace))
    registry.register(GlobTool(workspace, cancellation=cancellation))
    registry.register(BashTool(workspace, background))
    hooks = HookManager()
    holder = {}
    def ask(name, args, reason):
        if cancellation is not None and cancellation.is_set():
            return False
        if mode.value == "auto":
            return True
        if approval:
            return approval(name, args, reason)
        if owner == "lead" and holder.get("agent") and holder["agent"].interactive:
            return PermissionHook.ask_user(name, args, reason)
        return False
    hooks.register("PreToolUse", PermissionHook(workspace, ask=ask, confirm_shell=True, allow_commands=allow_commands))
    def large_output(name, args, output):
        if len(output) > 100_000:
            if events:
                events("notice", content=f"Large output from {name}; full results remain in context.")
            else:
                print(f"[hook] Large output from {name}")
    hooks.register("PostToolUse", large_output)
    todo = TodoTool()
    skills = SkillLoader(workspace)
    registry.register(todo)
    registry.register(skills)
    registry.register(CompactTool())
    if llm is None:
        from repopilot.llm.client import LLMClient
        llm = LLMClient(workspace, cancellation=cancellation)
    llm.system_prompt += ("\nPlan multi-step work with todo_write and keep progress current. "
                          "Load applicable skills before using their instructions.\nSkills:\n" + skills.catalog())
    if child:
        llm.system_prompt += "\nYou are a subagent. Complete only the delegated task and return a concise factual summary."
    else:
        registry.register(TaskTool(lambda: build_agent(workspace, child=True, owner="subagent", state_workspace=state_workspace, approval=ask, allow_commands=allow_commands, events=events, cancellation=cancellation, mode=mode)))
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
    agent = Agent(llm, registry, hooks, context, max_turns=30 if child else 100, events=events, cancellation=cancellation)
    agent.approval_mode = mode
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
        teams = TeamManager(workspace, tasks, lambda cwd, name: build_agent(cwd, child=True, owner=name, state_workspace=workspace, allow_commands=allow_commands, events=events, mode=mode))
        for tool in teams.tools():
            registry.register(tool)
        agent.teams = teams
        agent.event_sources.append(lambda: teams.bus.drain("lead"))
        def workflow_runner(prompt):
            worker = build_agent(workspace, child=True, owner="workflow", state_workspace=state_workspace, allow_commands=allow_commands, events=events, cancellation=cancellation, mode=mode)
            try:
                return worker.run([{"role": "user", "content": prompt}], prompt)
            finally:
                worker.background.close()
                worker.mcp.close()
        workflows = WorkflowRuntime(workspace, workflow_runner)
        workflows.register("review-changes", "Audit and verify supplied changes.", review_changes,
                           {"type": "object", "properties": {"changes": {"type": "string", "minLength": 1}}, "required": ["changes"], "additionalProperties": False})
        registry.register(workflows.tool())
        agent.workflows = workflows
        pending = lambda: background.pending() or any(s["status"] in ("WORK", "AWAITING_PLAN") for s in teams.list())
        goal = GoalController(workspace, llm.decide, pending)
        hooks.register("Stop", goal.stop)
        agent.goal = goal
        llm.system_prompt += "\nGoals are checked independently. Preserve concrete commands, exit codes and test results in the conversation. Never claim validation without evidence."
    if owner != "lead":
        llm.system_prompt += f"\nYour host identity is {owner}. Complete your assigned task using complete_task with its exact ID, only after validation."
    return agent


