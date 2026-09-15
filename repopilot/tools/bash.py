import os
import subprocess
from .base import Tool

class BashTool(Tool):
    name="bash"
    description="Run shell command"

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
        dangerous=["rm -rf /","shutdown","reboot"]
        if any(d in command for d in dangerous):
            return "Error: Dangerous command blocked"
        try:
            r=subprocess.run(command, shell=True, cwd=os.getcwd(),
                           capture_output=True, text=True, errors="replace", timeout=120)
            out = (r.stdout + r.stderr).strip()
            return out[:50000] if out else "(no output)"
        except subprocess.TimeoutExpired:
            return "Error: Timeout (120s)"
        except (FileNotFoundError, OSError) as e:
            return f"Error: {e}"