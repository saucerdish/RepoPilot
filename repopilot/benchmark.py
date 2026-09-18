"""Persistent, independently graded repository-task benchmark with bounded workers."""

import argparse
import hashlib
import json
import os
import statistics
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

from .benchmark_cases import CASES
from .runtime.background import BackgroundManager


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True).stdout


def command():
    return f'"{sys.executable}" -B -m unittest discover -s tests -v'


def prepare(root, case):
    root.mkdir(parents=True)
    files = {"pkg/__init__.py": "", "tests/test_public.py": case["visible"],
             "README.md": f"# {case['title']}\n\n{case['prompt']}\n\nRun: {command()}\n",
             ".gitignore": ".repopilot/\n__pycache__/\n", **case["files"]}
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    git(root, "init")
    git(root, "add", ".")
    git(root, "-c", "user.name=Benchmark", "-c", "user.email=benchmark@example.invalid", "commit", "-m", "initial failing fixture")
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in files}


def validate_public(root):
    return subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)


def grade(root, case, path):
    # Create acceptance tests only after the worker has stopped.
    source = "import sys\nfrom pathlib import Path\nROOT = Path(sys.argv[1]).resolve()\nsys.path.insert(0, str(ROOT))\n" + case["hidden"]
    source += '\nif __name__ == "__main__":\n    unittest.main(argv=[sys.argv[0]], verbosity=2)\n'
    path.write_text(source, encoding="utf-8")
    return subprocess.run([sys.executable, "-I", "-B", str(path), str(root)], cwd=path.parent, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)


def worker(root, name, result_path, max_turns):
    from dotenv import load_dotenv
    from main import build_agent
    from .runtime.host import AgentHost
    from .llm.telemetry import collect_metrics
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    case = next(case for case in CASES if case["name"] == name)
    result = {"name": name, "error": None}
    host = None
    started = time.monotonic()
    with collect_metrics() as metrics:
        try:
            agent = build_agent(root, approval=lambda *args: False, allow_commands=[command(), "git status --short", "git diff", "git diff --stat"])
            agent.max_turns = max_turns
            host = AgentHost(agent)
            if case.get("prefill"):
                host.history.append({"role": "user", "content": "Earlier investigation notes, reference only. Keep current user requirements authoritative.\n" + "Old unrelated diagnostic: cache entry loaded successfully.\n" * 1700})
            events = []
            agent.hooks.register("PreToolUse", lambda tool, args: events.append({"type": "tool", "name": tool}))
            prompt = (case["prompt"] + f"\nAllowed modified/created files: {case['allowed']}. Never change tests, README or Git metadata. "
                      f"Work on this repository yourself. Read relevant files, plan steps, implement and run exactly: {command()}. "
                      "It must exit 0. Preserve actual test evidence. Keep working until the requirements are met.")
            response = host.submit("/goal " + prompt)
            # Result notifications may arrive after a deferred Stop decision.
            while agent.goal.state and agent.goal.state["active"] and agent.goal.state["status"] == "deferred":
                host.poll()
                time.sleep(.2)
            result.update(response=response, model=agent.llm.model, goal=agent.goal.state,
                          tool_calls=len(events), tools=[event["name"] for event in events],
                          transcripts=len(list(root.glob(".repopilot/runs/**/*.json"))))
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            if host:
                try:
                    (root.parent / "conversation.json").write_text(json.dumps(host.history, ensure_ascii=False, indent=2), encoding="utf-8")
                finally:
                    host.close()
            result.update(seconds=round(time.monotonic() - started, 2), metrics=metrics.summary())
            result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def run(output_root, timeout=360, max_turns=20, selected=None, resume_run=None):
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    run_dir = resume_run.resolve() if resume_run else output_root / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
    if not resume_run:
        run_dir.mkdir()
    report = {"description": "Single-attempt generated repository benchmark, not a large real-world benchmark", "timeout_seconds": timeout,
              "max_turns": max_turns, "run_dir": str(run_dir), "supervision_resumed": bool(resume_run), "cases": []}
    for case in CASES:
        if selected and case["name"] != selected:
            continue
        case_dir = run_dir / case["name"]
        case_dir.mkdir(exist_ok=True)
        root = case_dir / "repository"
        result_path = case_dir / "worker-result.json"
        manifest = case_dir / "fingerprints.json"
        if not root.exists():
            fingerprints = prepare(root, case)
            manifest.write_text(json.dumps(fingerprints), encoding="utf-8")
            before = validate_public(root)
            (case_dir / "initial-tests.txt").write_text(before.stdout + before.stderr, encoding="utf-8")
            if before.returncode == 0:
                raise ValueError(f"Invalid benchmark fixture: {case['name']} already passes")
        else:
            fingerprints = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else None
            if not result_path.exists():
                raise ValueError("Existing incomplete worker requires inspection; use a new run rather than overwrite its repository")
        print(f"{'Reusing completed worker' if result_path.exists() else 'Starting'} {case['name']} (up to {timeout}s)", flush=True)
        options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        timed_out = False
        started = time.monotonic()
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
        if not result_path.exists():
            execute_worker(case_dir, root, case, result_path, max_turns, timeout, options, env)
        result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {"name": case["name"], "error": "Worker timed out or exited without results", "timed_out": True}
        timed_out = result.get("timed_out", False)
        result.setdefault("seconds", round(time.monotonic() - started, 2))
        try:
            public = validate_public(root)
            hidden = grade(root, case, case_dir / "acceptance.py")
            (case_dir / "public-tests.txt").write_text(public.stdout + public.stderr, encoding="utf-8")
            (case_dir / "acceptance-tests.txt").write_text(hidden.stdout + hidden.stderr, encoding="utf-8")
            result.update(public_exit=public.returncode, hidden_exit=hidden.returncode)
        except subprocess.TimeoutExpired:
            result.update(public_exit=None, hidden_exit=None, validation_error="Validation timed out")
        changed = sorted(set(git(root, "diff", "HEAD", "--name-only").splitlines() + git(root, "ls-files", "--others", "--exclude-standard").splitlines()))
        forbidden = [name for name, fingerprint in (fingerprints or {}).items() if name not in case["allowed"] and
                     (not (root / name).is_file() or hashlib.sha256((root / name).read_bytes()).hexdigest() != fingerprint)]
        if fingerprints is None:
            forbidden = [name for name in changed if name not in case["allowed"]]
        scope_ok = not forbidden and set(changed).issubset(case["allowed"])
        goal_completed = (result.get("goal") or {}).get("status") == "completed"
        functional_pass = result.get("public_exit") == 0 and result.get("hidden_exit") == 0 and scope_ok
        result.update(changed_files=changed, protected_changes=forbidden, scope_ok=scope_ok, functional_pass=functional_pass,
                      goal_completed=goal_completed, passed=functional_pass and goal_completed and not result.get("error") and not timed_out)
        result["failure_category"] = classify(result)
        (case_dir / "diff.patch").write_text(git(root, "diff", "HEAD"), encoding="utf-8")
        report["cases"].append(result)
        write_report(run_dir, report)
        print(f"{case['name']}: {'PASS' if result['passed'] else result['failure_category']}, {result['seconds']}s", flush=True)
    write_report(run_dir, report)
    print(f"Report: {run_dir / 'report.md'}", flush=True)
    return report


def classify(result):
    if result["passed"]:
        return None
    if result.get("timed_out"):
        return "timeout"
    if any(s in (result.get("error") or "") for s in ("APIConnectionError", "APITimeoutError", "RateLimitError")):
        return "infrastructure"
    if result.get("error"):
        return "execution_error"
    if not result["scope_ok"]:
        return "scope_violation"
    return "acceptance_failure" if not result["functional_pass"] else "goal_incomplete"


def execute_worker(case_dir, root, case, result_path, max_turns, timeout, options, env):
    started = time.monotonic()
    timed_out = False
    with (case_dir / "agent.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, "-u", "-B", "-m", "repopilot.benchmark", "--worker", str(root), "--case", case["name"], "--result", str(result_path), "--max-turns", str(max_turns)],
                                   cwd=Path(__file__).resolve().parents[1], stdout=log, stderr=subprocess.STDOUT, env=env, **options)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            BackgroundManager.terminate(process)
            process.wait(timeout=10)
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {"name": case["name"], "error": "Worker timed out" if timed_out else "Worker exited without results"}
    result.update(seconds=round(time.monotonic() - started, 2), timed_out=timed_out)
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

def write_report(run_dir, report):
    cases = report["cases"]
    completed = len(cases)
    report["summary"] = {"passed": sum(c["passed"] for c in cases), "total": completed,
                         "functional_passes": sum(c["functional_pass"] for c in cases),
                         "infrastructure_failures": sum(c["failure_category"] == "infrastructure" for c in cases),
                         "goal_false_positives": sum(c["goal_completed"] and not c["functional_pass"] for c in cases),
                         "median_seconds": round(statistics.median(c["seconds"] for c in cases), 2) if cases else None,
                         "total_tokens": sum(c.get("metrics", {}).get("total_tokens", 0) for c in cases)}
    (run_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# RepoPilot 仓库任务性能测试", "", report["description"], "", "每项一次尝试；模型内部有限重试计入耗时与调用数。验收测试在执行结束后生成。", "",
             "| 任务 | 严格通过 | 公开测试 | 独立验收 | 范围合规 | Goal | 秒 | API 调用 | Token |", "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for case in cases:
        metrics = case.get("metrics", {})
        lines.append(f"| {case['name']} | {case['passed']} | {case.get('public_exit')} | {case.get('hidden_exit')} | {case['scope_ok']} | {(case.get('goal') or {}).get('status')} | {case['seconds']} | {metrics.get('api_calls','unavailable')} | {metrics.get('total_tokens','unavailable')} |")
    lines += ["", f"严格通过：{report['summary']['passed']}/{completed}；功能通过：{report['summary']['functional_passes']}/{completed}。",
              f"基础设施失败：{report['summary']['infrastructure_failures']}；Goal 误报：{report['summary']['goal_false_positives']}。", "",
              "Token 仅包含 API 返回的 usage，失败请求可能没有 usage；未据此计算金额。样例数量少且为生成仓库，不能代表大型真实仓库完成率。", "", "## 失败诊断", ""]
    for case in cases:
        if not case["passed"]:
            lines.append(f"- {case['name']}: {case['failure_category']}; {case.get('error') or '查看 acceptance-tests.txt / public-tests.txt / conversation.json'}")
    if report.get("supervision_resumed"):
        lines += ["", "监督进程曾恢复；已完成 worker 只重新验收，未重新执行模型任务。"]
    if report.get("supervision_note"):
        lines += ["", report["supervision_note"]]
    (run_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=Path("D:/学习/RepoPilot-agent-tests"))
    parser.add_argument("--timeout", type=int, default=360)
    parser.add_argument("--max-turns", type=int, default=20)
    parser.add_argument("--case", choices=[case["name"] for case in CASES])
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--resume-run", type=Path, help="reuse completed worker results after interrupted supervision; never rerun them")
    args = parser.parse_args()
    if args.worker:
        worker(args.worker, args.case, args.result, args.max_turns)
        return 0
    report = run(args.output_root, args.timeout, args.max_turns, args.case, args.resume_run)
    return 0 if report["summary"]["passed"] == report["summary"]["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
