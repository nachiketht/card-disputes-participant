"""Draft the development cases from Blob into Postgres with approach 3.

The mock stays on loopback in this same process tree. The model provider is left
unset so an API key can select it. This does not reset or clear Postgres.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOCK_URL = "http://127.0.0.1:8765"
PATHS = ROOT / "submissions" / "classification" / "3"


def require_backends() -> None:
    if os.environ.get("CASES_BACKEND") != "blob" or os.environ.get("LEDGER_BACKEND") != "postgres":
        raise RuntimeError("backend")


def child_env() -> dict[str, str]:
    require_backends()
    env = dict(os.environ)
    env.pop("POSTGRES_TEST_URL", None)
    env.pop("LLM_PROVIDER", None)
    env["REVIEW_BACKEND"] = "postgres"
    env["FEATURE_LLM_REVIEW"] = "off"
    env["MOCK_BASE_URL"] = MOCK_URL
    return env


def mock_argv() -> list[str]:
    return [sys.executable, str(ROOT / "tools" / "runtime.py"), "serve", "--port", "8765"]


def copilot_argv() -> list[str]:
    return ["dispute-copilot", "run-all", "--approach", "3"]


def print_paths(folder: Path) -> None:
    """Print case id, path, final class, and route. Other fields stay out of the log."""
    if not folder.is_dir():
        return
    for path in sorted(folder.glob("*.json")):
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            continue
        if not isinstance(loaded, dict):
            continue
        case_id = loaded.get("case_id")
        chosen = loaded.get("path")
        final_class = loaded.get("final_class")
        route = loaded.get("route")
        fields = (case_id, chosen, final_class, route)
        if not all(isinstance(item, str) for item in fields):
            continue
        print(f"{case_id} {chosen} {final_class} {route}")


def run_approach3(start, wait_healthy, run_copilot, stop, report) -> int:
    env = child_env()
    proc = start(mock_argv(), env)
    try:
        wait_healthy()
        code = run_copilot(copilot_argv(), env)
        if code == 0:
            report()
        return code
    finally:
        stop(proc)


def wait_healthy(url: str = MOCK_URL + "/health", attempts: int = 50) -> None:
    import time

    last: Exception | None = None
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError) as exc:
            last = exc
        time.sleep(0.2)
    raise RuntimeError("health") from last


def main() -> int:
    import subprocess

    def start(argv: list[str], env: dict[str, str]):
        return subprocess.Popen(argv, env=env, cwd=ROOT)

    def run_copilot(argv: list[str], env: dict[str, str]) -> int:
        completed = subprocess.run(argv, env=env, cwd=ROOT, check=False)
        return completed.returncode

    def stop(proc) -> None:
        if proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    try:
        return run_approach3(start, wait_healthy, run_copilot, stop, lambda: print_paths(PATHS))
    except Exception as exc:
        print(type(exc).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
