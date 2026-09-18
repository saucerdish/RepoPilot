import subprocess
from pathlib import Path
from .base import Tool

class BashTool(Tool):
    name="bash"
    description="Run shell command"

    def __init__(self, workspace: Path, background=None):
        self.workspace = workspace.resolve()
        self.background = background

    def schema(self):
        schema = super().schema()
        schema["function"]["parameters"]["properties"] = {
            "command": {
                "type": "string",
                "description": "The shell command to execute",
            }
        }
        schema["function"]["parameters"]["required"] = ["command"]
        schema["function"]["parameters"]["properties"]["run_in_background"] = {"type": "boolean"}
        return schema
    
    def run(self, command, run_in_background=False):
        try:
            if self.background:
                if run_in_background is True:
                    return self.background.start(command, self.workspace)
                return self.background.execute(command, self.workspace)
            r=subprocess.run(command, shell=True, cwd=self.workspace,
                           capture_output=True, text=True, errors="replace", timeout=120)
            out = (r.stdout + r.stderr).strip()
            return f"Command: {command}\nExit code: {r.returncode}\n{out}"
        except subprocess.TimeoutExpired:
            return "Error: Timeout (120s)"
        except (FileNotFoundError, OSError) as e:
            return f"Error: {e}"
