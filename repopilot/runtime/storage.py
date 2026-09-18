import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Database:
    def __init__(self, workspace: Path):
        self.path = workspace.resolve() / ".repopilot" / "state.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        try:
            with connection:
                yield connection
        finally:
            connection.close()
