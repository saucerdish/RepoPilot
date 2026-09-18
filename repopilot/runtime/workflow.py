"""Host-registered workflows with durable semantic call caching."""

import asyncio
import hashlib
import json
import re
import uuid

from .storage import Database
from repopilot.tools.service import ServiceTool


def validate(value, schema):
    """Small, explicit JSON-schema subset used by registered workflows."""
    supported = {"type", "properties", "required", "additionalProperties", "items", "enum", "minLength", "maxItems", "description"}
    if set(schema) - supported:
        raise ValueError("Unsupported workflow schema keyword")
    kinds = {"object": dict, "array": list, "string": str, "boolean": bool, "integer": int, "number": (int, float), "null": type(None)}
    kind = schema.get("type")
    if kind and (kind not in kinds or not isinstance(value, kinds[kind]) or kind in ("integer", "number") and isinstance(value, bool)):
        raise ValueError(f"Expected {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError("Value outside enum")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if not set(schema.get("required", [])).issubset(value):
            raise ValueError("Missing required fields")
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            raise ValueError("Unexpected fields")
        for key in value.keys() & properties.keys():
            validate(value[key], properties[key])
    if isinstance(value, list):
        if len(value) > schema.get("maxItems", float("inf")):
            raise ValueError("Too many items")
        for item in value:
            validate(item, schema.get("items", {}))
    if isinstance(value, str) and len(value) < schema.get("minLength", 0):
        raise ValueError("String too short")
    return value


class WorkflowContext:
    def __init__(self, runtime, run_id, name, version, depth=0):
        self.runtime, self.run_id, self.name, self.version, self.depth = runtime, run_id, name, version, depth
        self.events, self.locks = [], {}

    def phase(self, title):
        self.events.append({"type": "phase", "title": title})

    def log(self, message):
        self.events.append({"type": "log", "message": message})

    async def agent(self, prompt, schema=None, label="agent", phase=None):
        basis = json.dumps([self.name, self.version, label, prompt, schema], sort_keys=True, ensure_ascii=False)
        key = hashlib.sha256(basis.encode()).hexdigest()
        lock = self.locks.setdefault(key, asyncio.Lock())
        async with lock:
            with self.runtime.db.connect() as conn:
                cached = conn.execute("SELECT value FROM workflow_calls WHERE run_id=? AND key=?", (self.run_id, key)).fetchone()
            if cached:
                value = json.loads(cached["value"])
                if schema:
                    validate(value, schema)
                self.events.append({"type": "agent", "label": label, "status": "cached"})
                return value
            value = None
            for attempt in range(2):
                self.events.append({"type": "agent", "label": label, "status": "started", "phase": phase})
                text = prompt + ("\nReturn only JSON matching this schema: " + json.dumps(schema) if schema else "")
                if attempt:
                    text += "\nPrevious output was invalid. Return strictly valid JSON."
                output = await asyncio.to_thread(self.runtime.runner, text)
                try:
                    value = validate(json.loads(output), schema) if schema else output
                    break
                except (ValueError, TypeError):
                    if attempt:
                        raise ValueError(f"Invalid structured output from {label}")
            with self.runtime.db.connect() as conn:
                conn.execute("INSERT INTO workflow_calls VALUES (?,?,?)", (self.run_id, key, json.dumps(value, ensure_ascii=False)))
            self.events.append({"type": "agent", "label": label, "status": "completed"})
            return value

    async def parallel(self, thunks):
        results = await asyncio.gather(*(thunk() for thunk in thunks), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result
        return results

    async def pipeline(self, items, *stages):
        async def run_item(item, index):
            value = item
            for stage in stages:
                value = await stage(value, item, index)
            return value
        return await self.parallel([lambda item=item, index=index: run_item(item, index) for index, item in enumerate(items)])

    async def workflow(self, name, args):
        if self.depth >= 1:
            raise ValueError("Nested workflows limited to one level")
        definition = self.runtime.registry[name]
        validate(args, definition["input_schema"])
        nested = WorkflowContext(self.runtime, self.run_id, name, definition["version"], self.depth + 1)
        result = await definition["handler"](nested, args)
        self.events.extend(nested.events)
        return result


class WorkflowRuntime:
    def __init__(self, workspace, runner):
        self.db, self.runner = Database(workspace), runner
        self.root = workspace.resolve() / ".repopilot" / "workflows"
        self.registry = {}
        with self.db.connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS workflow_calls (run_id TEXT, key TEXT, value TEXT, PRIMARY KEY(run_id,key))")

    def register(self, name, description, handler, input_schema, version="1"):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) or not description or name in self.registry:
            raise ValueError("Invalid or duplicate workflow metadata")
        if not asyncio.iscoroutinefunction(handler):
            raise ValueError("Workflow handler must be an async function")
        self.registry[name] = {"description": description, "handler": handler, "input_schema": input_schema, "version": version}

    def run(self, name, args, resume_from_run_id=None):
        if name not in self.registry:
            raise ValueError("Unknown registered workflow")
        definition = self.registry[name]
        validate(args, definition["input_schema"])
        identifier = resume_from_run_id or uuid.uuid4().hex
        if not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise ValueError("Invalid workflow run ID")
        self.root.mkdir(parents=True, exist_ok=True)
        snapshot = self.root / f"{identifier}.json"
        lock = self.root / f"{identifier}.lock"
        with lock.open("x", encoding="utf-8") as lease:
            lease.write("Run in progress; inspect before removing a stale lock.")
        try:
            if resume_from_run_id:
                saved = json.loads(snapshot.read_text(encoding="utf-8"))
                if saved["name"] != name or saved["args"] != args:
                    raise ValueError("Resume must use the original workflow name and args")
            context = WorkflowContext(self, identifier, name, definition["version"])
            result = {"run_id": identifier, "name": name, "args": args, "status": "running", "events": [{"type": "task_started"}]}
            self._save(snapshot, result)
            try:
                output = asyncio.run(definition["handler"](context, args))
                result.update(status="completed", output=output)
            except Exception as exc:
                result.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            result["events"] += context.events + [{"type": "task_notification", "status": result["status"]}]
            self._save(snapshot, result)
            return result
        finally:
            lock.unlink()

    @staticmethod
    def _save(path, value):
        import os
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)

    def tool(self):
        return ServiceTool("Workflow", "Run or resume a host-registered workflow; no model-generated scripts.", self.run,
                           {"name": {"type": "string", "enum": list(self.registry)}, "args": {"type": "object"}, "resume_from_run_id": {"type": "string"}}, ["name", "args"])


async def review_changes(context, args):
    context.phase("Review")
    schema = {"type": "object", "properties": {"findings": {"type": "array", "items": {"type": "string"}}}, "required": ["findings"], "additionalProperties": False}
    async def audit(value, dimension, index):
        return await context.agent(f"Review changes for {dimension}. Report concrete actionable findings, no invented facts.\n{args['changes']}", schema, label=f"audit:{dimension}")
    async def verify(audited, dimension, index):
        return await context.agent(f"Verify these findings against changes, discard unsupported findings.\nChanges: {args['changes']}\nFindings: {json.dumps(audited)}", schema, label=f"verify:{dimension}")
    results = await context.pipeline(["correctness", "regressions"], audit, verify)
    return {"findings": [finding for result in results for finding in result["findings"]]}
