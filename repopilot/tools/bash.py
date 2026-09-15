import subprocess
from pathlib import Path
from .base import Tool

class BashTool(Tool):
    name="bash"
    description="Run shell command"

    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()

    def schema(self):
        schema = super().schema()
        schema["function"]["parameters"]["properties"] = {
            "command": {
                "type": "string",
                "description": "The shell command to execute",
            }
        }
        schema["function"]["parameters"]["required"] = ["command"]
        return schema
    
    def run(self, command):
        try:
            r=subprocess.run(command, shell=True, cwd=self.workspace,
                           capture_output=True, text=True, errors="replace", timeout=120)
            out = (r.stdout + r.stderr).strip()
            return out[:50000] if out else "(no output)"
        except subprocess.TimeoutExpired:
            return "Error: Timeout (120s)"
        except (FileNotFoundError, OSError) as e:
            return f"Error: {e}"
