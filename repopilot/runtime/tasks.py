import json
import uuid

from .storage import Database
from repopilot.tools.service import ServiceTool


class TaskStore:
    def __init__(self, workspace):
        self.db = Database(workspace)
        with self.db.connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, record TEXT NOT NULL)")

    @staticmethod
    def _load(conn, task_id):
        row = conn.execute("SELECT record FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise ValueError(f"Unknown task: {task_id}")
        return json.loads(row["record"])

    @staticmethod
    def _save(conn, record):
        conn.execute("UPDATE tasks SET record=? WHERE id=?", (json.dumps(record), record["id"]))

    def create(self, subject, description=""):
        if not subject.strip():
            raise ValueError("Subject must not be empty")
        record = {"id": "task_" + uuid.uuid4().hex, "subject": subject, "description": description,
                  "status": "pending", "owner": None, "blockedBy": [], "cwd": None}
        with self.db.connect() as conn:
            conn.execute("INSERT INTO tasks VALUES (?,?)", (record["id"], json.dumps(record)))
        return record

    def get(self, task_id):
        with self.db.connect() as conn:
            return self._load(conn, task_id)

    def list(self):
        with self.db.connect() as conn:
            return [json.loads(row["record"]) for row in conn.execute("SELECT record FROM tasks ORDER BY rowid")]

    def update(self, task_id, addBlockedBy):
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            target = self._load(conn, task_id)
            if target["status"] != "pending" or target["owner"]:
                raise ValueError("Only unclaimed pending tasks may change dependencies")
            for dependency in addBlockedBy:
                def visit(identifier, seen):
                    if identifier == task_id:
                        raise ValueError("Dependency cycle")
                    if identifier in seen:
                        return
                    seen.add(identifier)
                    for parent in self._load(conn, identifier)["blockedBy"]:
                        visit(parent, seen)
                visit(dependency, set())
            target["blockedBy"] = sorted(set(target["blockedBy"] + addBlockedBy))
            self._save(conn, target)
            return target

    def claim(self, task_id, owner="lead"):
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            record = self._load(conn, task_id)
            if record["status"] != "pending" or not owner.strip():
                raise ValueError("Task is not claimable")
            if any(self._load(conn, dep)["status"] != "completed" for dep in record["blockedBy"]):
                raise ValueError("Task has incomplete dependencies")
            record.update(status="in_progress", owner=owner)
            self._save(conn, record)
            return record

    def complete(self, task_id, owner="lead"):
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            record = self._load(conn, task_id)
            if record["status"] != "in_progress" or record["owner"] != owner:
                raise ValueError("Only the current task owner may complete an active task")
            record["status"] = "completed"
            self._save(conn, record)
            return record

    def release(self, task_id, owner):
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            record = self._load(conn, task_id)
            if record["status"] == "in_progress" and record["owner"] == owner:
                record.update(status="pending", owner=None)
                self._save(conn, record)

    def bind(self, task_id, cwd):
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            record = self._load(conn, task_id)
            if record["status"] != "pending" or record["cwd"]:
                raise ValueError("Worktree requires an unclaimed, unbound task")
            record["cwd"] = str(cwd)
            self._save(conn, record)

    def tools(self, owner="lead", worker=False):
        string = {"type": "string"}
        tools = [ServiceTool("list_tasks", "List persistent tasks and dependency states.", self.list),
                 ServiceTool("get_task", "Read a persistent task.", self.get, {"task_id": string}, ["task_id"]),
                 ServiceTool("claim_task", "Atomically claim a ready task as this agent.", lambda task_id: self.claim(task_id, owner), {"task_id": string}, ["task_id"]),
                 ServiceTool("complete_task", "Complete a task owned by this agent.", lambda task_id: self.complete(task_id, owner), {"task_id": string}, ["task_id"])]
        if not worker:
            tools += [ServiceTool("create_task", "Create a node; obtain its returned ID before adding dependencies.", self.create, {"subject": string, "description": string}, ["subject"]),
                      ServiceTool("update_task", "Add dependency edges without creating cycles.", self.update, {"task_id": string, "addBlockedBy": {"type": "array", "items": string}}, ["task_id", "addBlockedBy"])]
        return tools
