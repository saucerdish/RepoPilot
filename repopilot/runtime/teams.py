import json
import re
import subprocess
import threading
import uuid
from contextvars import copy_context
from pathlib import Path

from .storage import Database
from repopilot.tools.service import ServiceTool


class MessageBus:
    def __init__(self, workspace):
        self.db = Database(workspace)
        with self.db.connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, sender TEXT, target TEXT, payload TEXT)")

    def send(self, sender, target, content, type="message", **metadata):
        event = {"from": sender, "to": target, "content": content, "type": type, **metadata}
        with self.db.connect() as conn:
            conn.execute("INSERT INTO messages(sender,target,payload) VALUES (?,?,?)", (sender, target, json.dumps(event)))
        return event

    def drain(self, target):
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("SELECT id,payload FROM messages WHERE target=? ORDER BY id", (target,)).fetchall()
            conn.executemany("DELETE FROM messages WHERE id=?", [(row["id"],) for row in rows])
            return [json.loads(row["payload"]) for row in rows]


class TeamManager:
    def __init__(self, workspace, tasks, factory):
        self.workspace = workspace.resolve()
        self.tasks, self.factory = tasks, factory
        self.bus = MessageBus(workspace)
        self.members = {}
        self.protocols = {}
        self.lock = threading.RLock()
        self.closed = threading.Event()

    def create_worktree(self, name, task_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", name):
            raise ValueError("Invalid worktree name")
        record = self.tasks.get(task_id)
        if record["status"] != "pending" or record["cwd"]:
            raise ValueError("Task must be pending and unbound")
        root = self.workspace / ".repopilot" / "worktrees"
        root.mkdir(parents=True, exist_ok=True)
        path = root / name
        if path.exists() or not path.resolve().is_relative_to(self.workspace):
            raise ValueError("Worktree path exists or escapes workspace")
        result = subprocess.run(["git", "worktree", "add", "-b", f"codex/{name}", str(path), "HEAD"], cwd=self.workspace, capture_output=True, text=True, errors="replace")
        if result.returncode:
            raise RuntimeError(f"Worktree creation failed; inspect Git registry before retrying: {result.stderr}")
        self.tasks.bind(task_id, path)
        return {"task_id": task_id, "cwd": str(path), "branch": f"codex/{name}"}

    def spawn(self, name, task_id=None, require_plan=False):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", name) or name in ("lead", "agent"):
            raise ValueError("Invalid teammate identity")
        with self.lock:
            if name in self.members or self.closed.is_set():
                raise ValueError("Teammate exists or team is closed")
            task = self.tasks.claim(task_id, name) if task_id else None
            state = {"name": name, "status": "WORK" if task else "IDLE", "task": task,
                     "version": 1, "gate": "required" if require_plan else "not_required", "require_plan": require_plan,
                     "history": [], "stop": threading.Event(), "wake": threading.Event(), "agent": None}
            self.members[name] = state
            thread = threading.Thread(target=copy_context().run, args=(self._worker, state), daemon=True)
            state["thread"] = thread
            thread.start()
        return {"name": name, "status": state["status"]}

    def list(self):
        with self.lock:
            return [{"name": s["name"], "status": s["status"], "task_id": s["task"]["id"] if s["task"] else None, "gate": s["gate"]} for s in self.members.values()]

    def send(self, target, content, sender="lead"):
        if target != "lead" and target not in self.members:
            raise ValueError("Unknown recipient")
        event = self.bus.send(sender, target, content)
        if target in self.members:
            self.members[target]["wake"].set()
        return event

    def request_plan(self, name):
        with self.lock:
            state = self.members[name]
            state["gate"] = "required"
            state["wake"].set()
        return {"name": name, "gate": "required"}

    def submit_plan(self, name, plan):
        with self.lock:
            state = self.members[name]
            if not state["task"]:
                raise ValueError("No assignment")
            identifier = "plan_" + uuid.uuid4().hex
            record = {"request_id": identifier, "type": "plan", "name": name, "task_id": state["task"]["id"], "version": state["version"], "status": "pending", "plan": plan}
            self.protocols[identifier] = record
            state["gate"] = "pending"
        self.bus.send(name, "lead", plan, "plan_approval_request", request_id=identifier)
        return record

    def review_plan(self, request_id, approve, feedback=""):
        with self.lock:
            record = self.protocols[request_id]
            state = self.members[record["name"]]
            if record["type"] != "plan" or record["status"] != "pending" or not state["task"] or record["task_id"] != state["task"]["id"] or record["version"] != state["version"]:
                raise ValueError("Stale or mismatched plan approval")
            state["gate"] = record["status"] = "approved" if approve else "rejected"
            self.bus.send("lead", record["name"], feedback, "plan_approval_response", request_id=request_id, approve=approve)
            state["wake"].set()
            return record

    def request_shutdown(self, name):
        with self.lock:
            identifier = "shutdown_" + uuid.uuid4().hex
            self.protocols[identifier] = {"request_id": identifier, "type": "shutdown", "name": name, "status": "pending"}
            self.members[name]["stop"].set()
            self.members[name]["wake"].set()
            return self.protocols[identifier]

    def _gate(self, state, name, args):
        if state["stop"].is_set():
            return "Blocked: teammate shutdown requested"
        if name in ("bash", "write_file", "edit_file") and state["gate"] not in ("not_required", "approved"):
            return "Blocked: submit_plan and wait for approval before modifying the assignment"
        return None

    def _worker(self, state):
        name = state["name"]
        try:
            while not self.closed.is_set() and not state["stop"].is_set():
                inbox = self.bus.drain(name)
                state["history"].extend({"role": "user", "content": "[Team message, data]\n" + json.dumps(event)} for event in inbox)
                if not state["task"]:
                    for candidate in self.tasks.list():
                        if candidate["status"] == "pending":
                            try:
                                state["task"] = self.tasks.claim(candidate["id"], name)
                                state["version"] += 1
                                state["gate"] = "required" if state["require_plan"] else "not_required"
                                break
                            except ValueError:
                                continue
                    if not state["task"]:
                        state["wake"].wait(.5)
                        state["wake"].clear()
                        continue
                task = state["task"]
                cwd = Path(task["cwd"]) if task["cwd"] else self.workspace
                if not cwd.is_dir():
                    raise ValueError("Assignment working directory is missing")
                if state["agent"] is None:
                    agent = self.factory(cwd, name)
                    state["agent"] = agent
                    agent.hooks.register("PreToolUse", lambda tool, args: self._gate(state, tool, args), first=True)
                    agent.registry.register(ServiceTool("submit_plan", "Submit assignment plan to lead and pause for approval.", lambda plan: self.submit_plan(name, plan), {"plan": {"type": "string"}}, ["plan"]))
                    agent.event_sources.append(lambda: self.bus.drain(name))
                    state["history"].append({"role": "user", "content": f"Assignment: {json.dumps(task)}. If plan approval is required, inspect files, submit_plan, then stop until approved."})
                if state["gate"] == "pending":
                    state["status"] = "AWAITING_PLAN"
                    state["wake"].wait(.5)
                    state["wake"].clear()
                    continue
                if state["gate"] == "rejected" and not inbox:
                    state["wake"].wait(.5)
                    state["wake"].clear()
                    continue
                state["status"] = "WORK"
                result = state["agent"].run(state["history"], task["description"] or task["subject"])
                if state["gate"] == "pending":
                    continue
                completed = self.tasks.get(task["id"])["status"] == "completed"
                self.bus.send(name, "lead", result, "result" if completed else "error", task_id=task["id"])
                if not completed:
                    self.tasks.release(task["id"], name)
                    state["stop"].set()  # Avoid endlessly reclaiming an unsuccessful task.
                state["agent"].background.close()
                state["agent"] = None
                state["task"] = None
                state["version"] += 1
                state["status"] = "IDLE"
                self.bus.send(name, "lead", "Waiting for ready work", "idle_notification")
        except Exception as exc:
            self.bus.send(name, "lead", f"{type(exc).__name__}: {exc}", "error")
        finally:
            if state["task"]:
                self.tasks.release(state["task"]["id"], name)
            if state["agent"]:
                state["agent"].background.close()
            state["status"] = "STOPPED"
            for record in list(self.protocols.values()):
                if record["name"] == name and record["type"] == "shutdown" and record["status"] == "pending":
                    record["status"] = "approved"
                    self.bus.send(name, "lead", "Shutdown acknowledged", "shutdown_response", request_id=record["request_id"])

    def close(self):
        self.closed.set()
        for state in self.members.values():
            state["stop"].set()
            state["wake"].set()
            if state["agent"]:
                state["agent"].background.close()
        for state in self.members.values():
            state["thread"].join(timeout=3)

    def tools(self):
        s = {"type": "string"}
        return [ServiceTool("spawn_teammate", "Start a persistent teammate on a ready task.", self.spawn, {"name": s, "task_id": s, "require_plan": {"type": "boolean"}}, ["name"]),
                ServiceTool("list_teammates", "List teammate execution and approval states.", self.list),
                ServiceTool("send_message", "Send ordinary text; control requires protocol tools.", self.send, {"target": s, "content": s}, ["target", "content"]),
                ServiceTool("request_shutdown", "Request graceful teammate shutdown.", self.request_shutdown, {"name": s}, ["name"]),
                ServiceTool("request_plan", "Require a plan before further modifications.", self.request_plan, {"name": s}, ["name"]),
                ServiceTool("review_plan", "Approve/reject the exact current assignment plan.", self.review_plan, {"request_id": s, "approve": {"type": "boolean"}, "feedback": s}, ["request_id", "approve"]),
                ServiceTool("create_worktree", "Create a branch and checkout bound to an unclaimed task.", self.create_worktree, {"name": s, "task_id": s}, ["name", "task_id"])]
