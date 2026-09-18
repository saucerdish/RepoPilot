import json
import re
import uuid

from .storage import Database


class MemoryStore:
    TYPES = ("user", "feedback", "project", "reference")

    def __init__(self, workspace):
        self.db = Database(workspace)
        with self.db.connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS memories (id TEXT PRIMARY KEY, name TEXT UNIQUE, type TEXT, description TEXT, body TEXT)")

    def records(self):
        with self.db.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM memories ORDER BY name")]

    def save(self, name, type, description, body, scope="persistent"):
        if scope != "persistent" or type not in self.TYPES:
            raise ValueError("Only persistent memory of a supported type may be stored")
        if not all(isinstance(v, str) and v.strip() for v in (name, description, body)):
            raise ValueError("Memory fields must be nonempty strings")
        if re.search(r"(?i)this (session|task)|current task|本次|当前任务|这次", body):
            raise ValueError("Temporary task constraints are not persistent memory")
        with self.db.connect() as conn:
            duplicate = conn.execute("SELECT id FROM memories WHERE body=?", (body,)).fetchone()
            if duplicate:
                return duplicate["id"]
            identifier = uuid.uuid4().hex
            conn.execute("INSERT INTO memories VALUES (?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET type=excluded.type, description=excluded.description, body=excluded.body", (identifier, name, type, description, body))
            return conn.execute("SELECT id FROM memories WHERE name=?", (name,)).fetchone()["id"]

    def recall(self, query, limit=5, budget=8000):
        tokens = set(re.findall(r"\w+", query.lower()))
        ranked = sorted(self.records(), key=lambda r: sum(t in (r["name"] + r["description"] + r["body"]).lower() for t in tokens), reverse=True)
        selected = [r for r in ranked if any(t in (r["name"] + r["description"] + r["body"]).lower() for t in tokens)][:limit]
        return json.dumps(selected, ensure_ascii=False)[:budget]

    def extract(self, messages, decide):
        candidates = decide("Extract at most five reusable memories. Return {records: [{name,type,description,body,scope}]}. scope must be persistent or current_task. Store only explicit stable preferences or verified facts; never infer temporary constraints as persistent.", json.dumps(messages, ensure_ascii=False)[-20000:])
        for record in candidates.get("records", [])[:5]:
            try:
                self.save(**record)
            except (TypeError, ValueError):
                continue

    def consolidate(self, records):
        # Transaction rollback keeps the old collection intact on any invalid item.
        with self.db.connect() as conn:
            conn.execute("DELETE FROM memories")
            for record in records:
                if record.get("type") not in self.TYPES or not all(isinstance(record.get(k), str) and record[k].strip() for k in ("name", "description", "body")):
                    raise ValueError("Invalid consolidated memory")
                conn.execute("INSERT INTO memories VALUES (?,?,?,?,?)", (uuid.uuid4().hex, record["name"], record["type"], record["description"], record["body"]))
