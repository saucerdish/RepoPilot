"""Small isolated Git tasks; demo is a scripted harness check, live measures a model."""

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace


CASES = [
    {"name": "slug-spaces", "source": 'def slugify(text):\n    return text.lower()\n',
     "old": 'return text.lower()', "new": 'return "-".join(text.lower().split())',
     "assertions": 'self.assertEqual(slugify(" Hello  World "), "hello-world")',
     "import": "slugify", "prompt": "Fix slugify: trim surrounding whitespace and collapse internal whitespace to one hyphen. Preserve lowercase output."},
    {"name": "zero-division", "source": 'def safe_divide(a, b):\n    return a / b\n',
     "old": 'return a / b', "new": 'return None if b == 0 else a / b',
     "assertions": 'self.assertIsNone(safe_divide(4, 0))\n        self.assertEqual(safe_divide(6, 2), 3)',
     "import": "safe_divide", "prompt": "Fix safe_divide to return None for a zero denominator and preserve ordinary division."},
    {"name": "fibonacci-zero", "source": 'def fibonacci(n):\n    a, b = 0, 1\n    for _ in range(n):\n        a, b = b, a + b\n    return b\n',
     "old": 'return b', "new": 'return a',
     "assertions": 'self.assertEqual(fibonacci(0), 0)\n        self.assertEqual(fibonacci(1), 1)\n        self.assertEqual(fibonacci(6), 8)',
     "import": "fibonacci", "prompt": "Fix fibonacci to use F(0)=0, F(1)=1 and return the nth value."},
]


class DemoLLM:
    system_prompt = "Scripted harness demonstration"

    def __init__(self, case, command):
        self.case, self.command, self.turn = case, command, 0

    def chat(self, messages, schemas):
        sequence = [("read_file", {"path": "module.py"}), ("bash", {"command": self.command}),
                    ("edit_file", {"path": "module.py", "old_text": self.case["old"], "new_text": self.case["new"]}),
                    ("bash", {"command": self.command})]
        index = self.turn
        self.turn += 1
        if index >= len(sequence):
            return SimpleNamespace(content="Applied targeted fix and ran tests.", tool_calls=None,
                                   model_dump=lambda **_: {"role": "assistant", "content": "Applied targeted fix and ran tests."})
        name, args = sequence[index]
        raw = json.dumps(args)
        identifier = f"demo-{index}"
        call = SimpleNamespace(id=identifier, function=SimpleNamespace(name=name, arguments=raw))
        return SimpleNamespace(content=None, tool_calls=[call], model_dump=lambda **_: {"role": "assistant", "tool_calls": [{"id": identifier, "type": "function", "function": {"name": name, "arguments": raw}}]})

    def summarize(self, text):
        return "Targeted fix applied; consult transcript for test evidence."

    def decide(self, instruction, text):
        if "Judge whether" in instruction:
            return {"ok": "Exit code: 0" in text, "impossible": False, "reason": "Verification result inspected"}
        return {"records": []}


def evaluate(mode="demo", output=None, selected=None):
    from main import build_agent
    from repopilot.runtime.host import AgentHost
    report = {"mode": mode, "description": "Scripted harness check; not model performance" if mode == "demo" else "Live model on three small generated Git tasks; not a broad benchmark", "cases": []}
    for case in CASES:
        if selected and case["name"] != selected:
            continue
        with tempfile.TemporaryDirectory(prefix="repopilot-eval-") as directory:
            root = Path(directory)
            (root / "tests").mkdir()
            (root / "module.py").write_text(case["source"], encoding="utf-8")
            test_source = f"import unittest\nfrom module import {case['import']}\n\nclass Tests(unittest.TestCase):\n    def test_contract(self):\n        {case['assertions']}\n"
            (root / "tests" / "test_contract.py").write_text(test_source, encoding="utf-8")
            (root / "README.md").write_text("Run python -m unittest discover -s tests -v. Modify module.py only.")
            (root / ".gitignore").write_text(".repopilot/\n__pycache__/\n")
            def git(*args):
                return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, errors="replace", check=True)
            git("init")
            git("add", ".")
            git("-c", "user.name=Evaluation", "-c", "user.email=evaluation@example.invalid", "commit", "-m", "fixture")
            argv = [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"]
            before = subprocess.run(argv, cwd=root, capture_output=True)
            command = f'"{sys.executable}" -B -m unittest discover -s tests -v'
            llm = DemoLLM(case, command) if mode == "demo" else None
            agent = build_agent(root, llm=llm, approval=lambda *args: False, allow_commands=[command])
            host = AgentHost(agent)
            started = time.monotonic()
            error = None
            try:
                host.submit(f"/goal {case['prompt']} Modify module.py only; do not modify tests. Run exactly this validation command and preserve its exit code: {command}. It must exit 0.")
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            finally:
                host.close()
            after = subprocess.run(argv, cwd=root, capture_output=True, text=True, errors="replace", timeout=30)
            changed = sorted(set(git("diff", "--name-only").stdout.splitlines() + git("ls-files", "--others", "--exclude-standard").stdout.splitlines()))
            tests_unchanged = (root / "tests" / "test_contract.py").read_text(encoding="utf-8") == test_source
            passed = before.returncode != 0 and after.returncode == 0 and tests_unchanged and changed == ["module.py"] and not error
            report["cases"].append({"name": case["name"], "passed": passed, "seconds": round(time.monotonic() - started, 2),
                                    "initial_exit": before.returncode, "final_exit": after.returncode, "changed_files": changed,
                                    "tests_unchanged": tests_unchanged, "goal_status": agent.goal.state, "error": error, "validation": after.stdout + after.stderr})
    report["passed"] = sum(case["passed"] for case in report["cases"])
    report["total"] = len(report["cases"])
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("demo", "live"), default="demo")
    parser.add_argument("--case", choices=[case["name"] for case in CASES])
    parser.add_argument("--output", type=Path, default=Path(".repopilot/evaluation.json"))
    args = parser.parse_args()
    if args.mode == "live":
        from dotenv import load_dotenv
        load_dotenv()
    report = evaluate(args.mode, args.output, args.case)
    print(f"{report['description']}: {report['passed']}/{report['total']}\nReport: {args.output.resolve()}")
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
