"""Draft the development cases from Blob into Postgres.

The mock stays on loopback in this same process tree. Approach 1 does not call a model.
"""

import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOCK_URL = "http://127.0.0.1:8765"


def require_backends() -> None:
    if os.environ.get("CASES_BACKEND") != "blob" or os.environ.get("LEDGER_BACKEND") != "postgres":
        raise RuntimeError("backend")


def child_env() -> dict[str, str]:
    require_backends()
    env = dict(os.environ)
    env.pop("POSTGRES_TEST_URL", None)
    env["REVIEW_BACKEND"] = "postgres"
    env["LLM_PROVIDER"] = "null"
    env["FEATURE_LLM_REVIEW"] = "off"
    env["MOCK_BASE_URL"] = MOCK_URL
    return env


def mock_argv() -> list[str]:
    return [sys.executable, str(ROOT / "tools" / "runtime.py"), "serve", "--port", "8765"]


def copilot_argv() -> list[str]:
    return ["dispute-copilot", "run-all", "--approach", "1"]


def run_batch(start, wait_healthy, run_copilot, stop) -> int:
    env = child_env()
    mock = mock_argv()
    copilot = copilot_argv()
    proc = start(mock, env)
    try:
        wait_healthy()
        return run_copilot(copilot, env)
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
        return run_batch(start, wait_healthy, run_copilot, stop)
    except Exception as exc:
        print(type(exc).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
