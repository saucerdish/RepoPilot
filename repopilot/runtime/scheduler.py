import json
import uuid
from datetime import datetime

from .storage import Database
from repopilot.tools.service import ServiceTool


def parse_cron(expression):
    fields = expression.split()
    ranges = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))
    if len(fields) != 5:
        raise ValueError("Cron requires five fields")
    parsed = []
    for field, (low, high) in zip(fields, ranges):
        values = set()
        for part in field.split(","):
            base, sep, step_text = part.partition("/")
            step = int(step_text) if sep else 1
            if step <= 0:
                raise ValueError("Cron step must be positive")
            if base == "*":
                start, end = low, high
            elif "-" in base:
                start, end = map(int, base.split("-"))
            else:
                start = end = int(base)
            if not low <= start <= end <= high:
                raise ValueError("Cron field out of range")
            values.update(range(start, end + 1, step))
        parsed.append(values)
    return parsed


class Scheduler:
    def __init__(self, workspace):
        self.db = Database(workspace)
        with self.db.connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS crons (id TEXT PRIMARY KEY, record TEXT)")

    def schedule(self, cron, prompt, recurring=True):
        parse_cron(cron)
        if not prompt.strip():
            raise ValueError("Prompt must not be empty")
        record = {"id": "cron_" + uuid.uuid4().hex, "cron": cron, "prompt": prompt,
                  "recurring": recurring, "pending_delivery": False, "last_fired": None}
        with self.db.connect() as conn:
            conn.execute("INSERT INTO crons VALUES (?,?)", (record["id"], json.dumps(record)))
        return record

    def list(self):
        with self.db.connect() as conn:
            return [json.loads(row["record"]) for row in conn.execute("SELECT record FROM crons")]

    def cancel(self, job_id):
        with self.db.connect() as conn:
            return {"cancelled": bool(conn.execute("DELETE FROM crons WHERE id=?", (job_id,)).rowcount)}

    def poll(self, moment=None):
        moment = moment or datetime.now()
        marker = moment.strftime("%Y-%m-%d %H:%M")
        values = (moment.minute, moment.hour, moment.day, moment.month, (moment.weekday() + 1) % 7)
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            due = []
            for row in conn.execute("SELECT record FROM crons").fetchall():
                record = json.loads(row["record"])
                if not record["pending_delivery"] and record["last_fired"] != marker and all(v in choices for v, choices in zip(values, parse_cron(record["cron"]))):
                    record.update(pending_delivery=True, last_fired=marker)
                    conn.execute("UPDATE crons SET record=? WHERE id=?", (json.dumps(record), record["id"]))
                if record["pending_delivery"]:
                    due.append(record)
            return due

    def ack(self, job_id):
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT record FROM crons WHERE id=?", (job_id,)).fetchone()
            if not row:
                return
            record = json.loads(row["record"])
            if record["recurring"]:
                record["pending_delivery"] = False
                conn.execute("UPDATE crons SET record=? WHERE id=?", (json.dumps(record), job_id))
            else:
                conn.execute("DELETE FROM crons WHERE id=?", (job_id,))

    def tools(self):
        string = {"type": "string"}
        return [ServiceTool("schedule_cron", "Schedule a persistent local-time prompt while the host is running.", self.schedule, {"cron": string, "prompt": string, "recurring": {"type": "boolean"}}, ["cron", "prompt"]),
                ServiceTool("list_crons", "List persistent scheduled prompts.", self.list),
                ServiceTool("cancel_cron", "Cancel a scheduled prompt.", self.cancel, {"job_id": string}, ["job_id"])]
