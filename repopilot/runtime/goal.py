import json
import time

from .storage import Database


class GoalController:
    def __init__(self, workspace, decide, pending=lambda: False):
        self.db, self.decide, self.pending = Database(workspace), decide, pending
        with self.db.connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS goals (identity TEXT PRIMARY KEY, record TEXT)")
            row = conn.execute("SELECT record FROM goals WHERE identity='lead'").fetchone()
        self.state = json.loads(row["record"]) if row else None

    def persist(self):
        with self.db.connect() as conn:
            conn.execute("INSERT INTO goals VALUES ('lead',?) ON CONFLICT(identity) DO UPDATE SET record=excluded.record", (json.dumps(self.state),))

    def set(self, criteria):
        if not criteria.strip():
            raise ValueError("Goal criteria must not be empty")
        self.state = {"criteria": criteria, "active": True, "status": "active", "checks": 0,
                      "started_at": time.time(), "reason": "Not evaluated yet"}
        self.persist()
        return self.state

    def clear(self):
        self.state = None
        self.persist()
        return "Goal cleared"

    def status(self):
        return json.dumps(self.state, ensure_ascii=False, indent=2) if self.state else "No active goal"

    def stop(self, messages):
        if not self.state or not self.state["active"]:
            return None
        if self.pending():
            self.state.update(status="deferred", reason="Waiting for background or teammate evidence")
            self.persist()
            return None
        self.state["checks"] += 1
        # Keep complete recent messages; oversized single messages keep head and tail.
        selected, budget = [], 30000
        for message in reversed(messages):
            text = json.dumps(message, ensure_ascii=False)
            if len(text) > budget:
                if not selected:
                    selected.append(text[:budget//2] + "\n[Middle omitted]\n" + text[-budget//2:])
                break
            selected.append(text)
            budget -= len(text)
        try:
            verdict = self.decide("Judge whether the stated software goal is achieved from concrete tool results. Do not execute tasks. Unsupported success claims are not evidence. Return {ok:boolean, impossible:boolean, reason:string}. Require actual verification results where criteria specify tests or commands.",
                                  json.dumps({"criteria": self.state["criteria"], "evidence": list(reversed(selected))}, ensure_ascii=False))
            if not isinstance(verdict, dict) or type(verdict.get("ok")) is not bool or type(verdict.get("impossible")) is not bool or not isinstance(verdict.get("reason"), str) or not verdict["reason"].strip() or verdict["ok"] and verdict["impossible"]:
                raise ValueError("Invalid goal evaluator verdict")
        except Exception as exc:
            self.state.update(status="error", reason=f"Evaluator failed: {type(exc).__name__}: {exc}")
            self.persist()
            raise RuntimeError(self.state["reason"]) from exc
        self.state["reason"] = verdict["reason"]
        if verdict["ok"]:
            self.state.update(status="completed", active=False)
        elif verdict["impossible"]:
            self.state.update(status="impossible", active=False)
        else:
            self.state["status"] = "active"
        self.persist()
        return None if not self.state["active"] else "Goal not yet met: " + verdict["reason"]

    def limited(self, reason):
        if self.state and self.state["active"]:
            self.state.update(status="limited", reason=reason)
            self.persist()
