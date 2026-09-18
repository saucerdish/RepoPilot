"""Minimal stdio MCP transport and host-controlled dynamic tool discovery."""

import json
import queue
import re
import subprocess
import threading

from repopilot.tools.service import ServiceTool
from .background import BackgroundManager


class StdioClient:
    def __init__(self, command, cwd, timeout=30):
        if not isinstance(command, list) or not command or not all(isinstance(s, str) for s in command):
            raise ValueError("MCP command must be an argv array")
        import os
        options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        self.process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1, **options)
        self.inbox, self.lock = queue.Queue(), threading.Lock()
        self.timeout, self.counter = timeout, 0
        def read():
            try:
                for line in self.process.stdout:
                    self.inbox.put(json.loads(line))
            except Exception as exc:
                self.inbox.put(exc)
            finally:
                self.inbox.put(EOFError("MCP server disconnected"))
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()
        try:
            initialized = self.request("initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "RepoPilot", "version": "0.1.0"}})
            if initialized.get("protocolVersion") not in ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"):
                raise ValueError("Unsupported MCP protocol version")
            self.write({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except Exception:
            self.close()
            raise

    def write(self, message):
        self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def request(self, method, params):
        import time
        with self.lock:
            self.counter += 1
            identifier = self.counter
            self.write({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params})
            deadline = time.monotonic() + self.timeout
            while True:
                if time.monotonic() > deadline:
                    self.close()
                    raise TimeoutError("MCP request deadline exceeded")
                try:
                    message = self.inbox.get(timeout=max(.001, deadline - time.monotonic()))
                except queue.Empty:
                    self.close()
                    raise TimeoutError("MCP request timed out")
                if isinstance(message, Exception):
                    raise message
                if "method" in message:
                    if "id" in message:
                        if message["method"] == "ping":
                            self.write({"jsonrpc": "2.0", "id": message["id"], "result": {}})
                        else:
                            self.write({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "Client capability unsupported"}})
                    continue
                if message.get("id") != identifier:
                    continue
                if "error" in message:
                    raise RuntimeError(str(message["error"]))
                return message["result"]

    def list_tools(self):
        tools, cursor = [], None
        seen = set()
        while True:
            result = self.request("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(result.get("tools", []))
            cursor = result.get("nextCursor")
            if not cursor:
                return tools
            if cursor in seen:
                raise ValueError("Repeated MCP pagination cursor")
            seen.add(cursor)

    def call(self, name, arguments):
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def close(self):
        BackgroundManager.terminate(self.process)
        self.process.wait(timeout=5)
        self.reader.join(timeout=2)
        for stream in (self.process.stdin, self.process.stdout):
            stream.close()


class MCPManager:
    def __init__(self, workspace, registry, servers=None, factory=StdioClient):
        self.workspace, self.registry = workspace, registry
        self.servers, self.factory = servers or {}, factory
        self.clients, self.policies = {}, {}

    def connect(self, name):
        if name in self.clients:
            return {"connected": name, "already_connected": True}
        if name not in self.servers:
            raise ValueError("MCP server must be configured by the host")
        config = self.servers[name]
        client = self.factory(config["command"], self.workspace)
        staged = []
        try:
            names = set(self.registry.tools)
            for definition in client.list_tools():
                raw = definition["name"]
                normalized = "mcp__" + re.sub(r"[^A-Za-z0-9_-]", "_", name) + "__" + re.sub(r"[^A-Za-z0-9_-]", "_", raw)
                if len(normalized) > 64 or normalized in names:
                    raise ValueError("MCP normalized name collision or length violation")
                names.add(normalized)
                schema = definition.get("inputSchema", {"type": "object"})
                tool = ServiceTool(normalized, definition.get("description", "External MCP tool"), lambda raw=raw, **args: client.call(raw, args), schema.get("properties"), schema.get("required"))
                tool.schema = lambda tool=tool, schema=schema: {"type": "function", "function": {"name": tool.name, "description": tool.description, "parameters": schema}}
                staged.append((tool, "allow" if raw in config.get("allow_tools", []) else "confirm"))
            for tool, policy in staged:
                self.registry.register(tool)
                self.policies[tool.name] = policy
            self.clients[name] = client
            return {"connected": name, "tools": [t.name for t, _ in staged]}
        except Exception:
            client.close()
            raise

    def permission(self, ask):
        def hook(name, args):
            if name.startswith("mcp__") and self.policies.get(name) != "allow":
                if not ask(name, args, "external tool requires host confirmation"):
                    return "Permission denied: external MCP tool"
            if name == "connect_mcp" and not ask(name, args, "launch configured local MCP server"):
                return "Permission denied: MCP connection"
            return None
        return hook

    def close(self):
        for client in self.clients.values():
            client.close()
        self.clients.clear()
