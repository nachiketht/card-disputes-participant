"""Run a smoke inside one container without touching the demo ledger.

The demo ledger is /state/run.sqlite. Demo drafts are /submissions.
This process refuses those paths. It does not approve and it does not clear the ledger.
"""

import argparse
import base64
import json
import os
import signal
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

DEMO_STATE_ROOT = Path("/state")
DEMO_SUBMISSIONS = Path("/submissions")
RESULT = "AZURE_CI_RESULT"
PORT_LINE = "AZURE_CI_PORT"
BUNDLE_BEGIN = "AZURE_CI_BUNDLE_BEGIN"
BUNDLE_END = "AZURE_CI_BUNDLE_END"


def under_banned(path: Path, root: Path) -> bool:
    resolved = path.resolve()
    banned = root.resolve()
    return resolved == banned or banned in resolved.parents


def guard(path: Path) -> None:
    if under_banned(path, DEMO_STATE_ROOT) or under_banned(path, DEMO_SUBMISSIONS):
        raise SystemExit(f"refusing demo path {path}")


def health(url: str, attempts: int = 50) -> bool:
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:
                if response.status == 200:
                    return True
        except (OSError, urllib.error.URLError):
            time.sleep(0.2)
    return False


def emit(code: int) -> int:
    print(f"{RESULT} code={code}", flush=True)
    return code


def child_env(submissions: Path, reports: Path, mock_url: str, data_dir: Path) -> dict:
    env = os.environ.copy()
    env.pop("LLM_API_KEY", None)
    env["LLM_PROVIDER"] = "null"
    env["FEATURE_LLM_REVIEW"] = "off"
    env["SUBMISSIONS_DIR"] = str(submissions)
    env["REPORTS_DIR"] = str(reports)
    env["DATA_DIR"] = str(data_dir)
    env["MOCK_BASE_URL"] = mock_url
    env["RUN_LABEL"] = "azure"
    return env


def precheck(fixture: Path, url: str) -> int:
    if not health(url, attempts=20):
        print("mock did not answer", file=sys.stderr)
        return emit(1)
    if fixture.exists():
        print("fixture is visible", file=sys.stderr)
        return emit(1)
    print("fixture is not visible")
    return emit(0)


def _daemon(argv: list[str], log_path: Path, pid_path: Path, workdir: Path) -> None:
    first = os.fork()
    if first > 0:
        _pid, status = os.waitpid(first, 0)
        if status != 0:
            raise SystemExit("side process did not detach")
        return
    os.setsid()
    second = os.fork()
    if second > 0:
        os._exit(0)
    os.chdir(workdir)
    pid_path.write_text(str(os.getpid()), encoding="utf-8")
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.close(fd)
    os.execv(argv[0], argv)


def start_side(data_dir: Path, state: Path, port: int, pid_path: Path, log_path: Path) -> int:
    guard(state)
    guard(pid_path)
    guard(log_path)
    fixture = data_dir / "dataset.json"
    if not fixture.is_file():
        print("side mock has no fixture", file=sys.stderr)
        return emit(1)
    if pid_path.is_file():
        _stop_pid(pid_path, state)
    argv = [
        sys.executable,
        "tools/runtime.py",
        "serve",
        "--data",
        str(data_dir),
        "--state",
        str(state),
        "--port",
        str(port),
    ]
    _daemon(argv, log_path, pid_path, Path.cwd())
    url = f"http://127.0.0.1:{port}/health"
    if not health(url):
        print("side mock did not answer on 127.0.0.1", file=sys.stderr)
        return emit(1)
    print(f"{PORT_LINE}={port}", flush=True)
    return emit(0)


def _stop_pid(pid_path: Path, state: Path) -> None:
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return
    if pid <= 1:
        return
    cmdline = Path(f"/proc/{pid}/cmdline")
    try:
        text = cmdline.read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
    except OSError:
        return
    if str(state) not in text or "runtime.py" not in text or "serve" not in text:
        print("refusing to stop a process that is not the side mock", file=sys.stderr)
        return
    os.kill(pid, signal.SIGTERM)
    for _ in range(25):
        try:
            os.kill(pid, 0)
        except OSError:
            break
        time.sleep(0.1)
    else:
        os.kill(pid, signal.SIGKILL)


def run_pass(data_dir: Path, fixture: Path, submissions: Path, reports: Path, mock_url: str) -> int:
    guard(submissions)
    guard(reports)
    if fixture.exists():
        print("fixture is visible", file=sys.stderr)
        return emit(1)
    if not health(mock_url.rstrip("/") + "/health", attempts=20):
        print("side mock did not answer", file=sys.stderr)
        return emit(1)
    submissions.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)
    env = child_env(submissions, reports, mock_url, data_dir)
    drafted = subprocess.run(
        [sys.executable, "-m", "dispute_copilot", "run-all", "--mock-url", mock_url, "--label", "azure"],
        cwd=Path.cwd(),
        env=env,
    )
    if drafted.returncode != 0:
        print("run-all did not write every case", file=sys.stderr)
        _pack(submissions, reports)
        return emit(drafted.returncode or 1)
    results = submissions / "results.json"
    try:
        rows = json.loads(results.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print("submission file is missing", file=sys.stderr)
        return emit(1)
    checks = []
    failed = False
    for item in rows:
        case_id = item.get("case_id") if isinstance(item, dict) else None
        if not isinstance(case_id, str):
            failed = True
            continue
        reviewed = subprocess.run(
            [sys.executable, "-m", "dispute_copilot", "agent-review", case_id, "--mock-url", mock_url, "--label", "azure"],
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
        )
        parsed = None
        try:
            parsed = json.loads(reviewed.stdout)
        except json.JSONDecodeError:
            parsed = None
        passed = isinstance(parsed, dict) and parsed.get("passed") is True and reviewed.returncode == 0
        checks.append({"case_id": case_id, "passed": passed, "exit": reviewed.returncode})
        if not passed:
            failed = True
    (reports / "checklist.json").write_text(json.dumps(checks, indent=2) + "\n", encoding="utf-8")
    subprocess.run(
        [sys.executable, "-m", "dispute_copilot", "metrics"],
        cwd=Path.cwd(),
        env=env,
    )
    _pack(submissions, reports)
    if failed:
        print("checklist did not pass", file=sys.stderr)
        return emit(1)
    return emit(0)


def evaluate(data_dir: Path, gold: Path, submission: Path, report: Path) -> int:
    guard(gold)
    guard(submission)
    guard(report)
    if not (data_dir / "dataset.json").is_file():
        print("evaluator fixture is missing", file=sys.stderr)
        return emit(1)
    proc = subprocess.run(
        [
            sys.executable,
            "tools/evaluate.py",
            "--data",
            str(data_dir),
            "--gold",
            str(gold),
            "--submission",
            str(submission),
            "--report",
            str(report),
        ],
        cwd=Path.cwd(),
        text=True,
    )
    if report.is_file():
        _emit_bytes(report.read_bytes())
    return emit(proc.returncode)


def cleanup(paths: list[Path], pid_path: Path | None, state: Path | None) -> int:
    for path in paths:
        guard(path)
    if pid_path is not None and state is not None and pid_path.is_file():
        guard(pid_path)
        guard(state)
        _stop_pid(pid_path, state)
    for path in paths:
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            _rm_tree(path)
    return emit(0)


def _rm_tree(path: Path) -> None:
    for child in sorted(path.rglob("*"), reverse=True):
        if child.is_symlink() or child.is_file():
            child.unlink()
        elif child.is_dir():
            child.rmdir()
    path.rmdir()


def _pack(submissions: Path, reports: Path) -> None:
    import io

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as archive:
        results = submissions / "results.json"
        if results.is_file():
            archive.add(results, arcname="results.json")
        if reports.is_dir():
            archive.add(reports, arcname="reports")
    _emit_bytes(buf.getvalue())


def _emit_bytes(payload: bytes) -> None:
    print(BUNDLE_BEGIN, flush=True)
    print(base64.b64encode(payload).decode("ascii"), flush=True)
    print(BUNDLE_END, flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("precheck")
    check.add_argument("--fixture", type=Path, required=True)
    check.add_argument("--url", required=True)
    side = commands.add_parser("start")
    side.add_argument("--data", type=Path, required=True)
    side.add_argument("--state", type=Path, required=True)
    side.add_argument("--port", type=int, required=True)
    side.add_argument("--pid", type=Path, required=True)
    side.add_argument("--log", type=Path, required=True)
    passed = commands.add_parser("run")
    passed.add_argument("--data", type=Path, required=True)
    passed.add_argument("--fixture", type=Path, required=True)
    passed.add_argument("--submissions", type=Path, required=True)
    passed.add_argument("--reports", type=Path, required=True)
    passed.add_argument("--url", required=True)
    scored = commands.add_parser("evaluate")
    scored.add_argument("--data", type=Path, required=True)
    scored.add_argument("--gold", type=Path, required=True)
    scored.add_argument("--submission", type=Path, required=True)
    scored.add_argument("--report", type=Path, required=True)
    clean = commands.add_parser("cleanup")
    clean.add_argument("--path", type=Path, action="append", default=[])
    clean.add_argument("--pid", type=Path)
    clean.add_argument("--state", type=Path)
    args = parser.parse_args(argv)
    if args.command == "precheck":
        return precheck(args.fixture, args.url)
    if args.command == "start":
        return start_side(args.data, args.state, args.port, args.pid, args.log)
    if args.command == "run":
        return run_pass(args.data, args.fixture, args.submissions, args.reports, args.url)
    if args.command == "evaluate":
        return evaluate(args.data, args.gold, args.submission, args.report)
    return cleanup(args.path, args.pid, args.state)


if __name__ == "__main__":
    sys.exit(main())
