import os
import queue
import signal
import subprocess
import threading
import uuid


class BackgroundManager:
    def __init__(self):
        self.results = queue.Queue()
        self.tasks = {}
        self.lock = threading.Lock()
        self.closed = False

    @staticmethod
    def terminate(process):
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def execute(self, command, cwd, timeout=120):
        options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        with self.lock:
            if self.closed:
                raise RuntimeError("Background runtime is closed")
            process = subprocess.Popen(command, shell=True, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace", **options)
            self.tasks.setdefault(threading.get_ident(), {})["process"] = process
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            return f"Command: {command}\nExit code: {process.returncode}\n{stdout}{stderr}"
        except subprocess.TimeoutExpired:
            self.terminate(process)
            stdout, stderr = process.communicate()
            return f"Command: {command}\nExit code: timeout\n{stdout}{stderr}"
        finally:
            self.terminate(process)
            with self.lock:
                self.tasks.pop(threading.get_ident(), None)

    def start(self, command, cwd):
        identifier = "bg_" + uuid.uuid4().hex
        entry = {"id": identifier, "command": command, "status": "running"}
        def worker():
            try:
                output = self.execute(command, cwd)
                entry["status"] = "completed" if "\nExit code: 0\n" in output else "failed"
            except Exception as exc:
                output = f"Error: {type(exc).__name__}: {exc}"
                entry["status"] = "failed"
            entry["output"] = output
            self.results.put({"type": "task_notification", **entry})
        with self.lock:
            if self.closed:
                raise RuntimeError("Background runtime is closed")
            self.tasks[identifier] = entry
        thread = threading.Thread(target=worker, daemon=True)
        entry["thread"] = thread
        thread.start()
        return f"Background task started: {identifier}"

    def collect(self):
        collected = []
        while True:
            try:
                event = self.results.get_nowait()
            except queue.Empty:
                return collected
            event.pop("thread", None)
            collected.append(event)

    def pending(self):
        with self.lock:
            return any(entry.get("status") == "running" for entry in self.tasks.values())

    def close(self):
        with self.lock:
            self.closed = True
            processes = [entry["process"] for entry in self.tasks.values() if "process" in entry]
            threads = [entry["thread"] for entry in self.tasks.values() if "thread" in entry]
        for process in processes:
            self.terminate(process)
        for thread in threads:
            thread.join(timeout=3)
