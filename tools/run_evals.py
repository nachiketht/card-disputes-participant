"""Start a loopback mock, draft the development cases, and print the scorecard.

The judge stays off. This file does not open the mock fixture.
"""

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.5) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def main() -> int:
    port = _port()
    state = tempfile.NamedTemporaryFile(prefix="evals-", suffix=".sqlite", delete=False)
    state.close()
    work = tempfile.TemporaryDirectory(prefix="evals-")
    submissions = Path(work.name) / "submissions"
    reports = Path(work.name) / "reports"
    submissions.mkdir()
    reports.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "LLM_PROVIDER": "null",
            "FEATURE_LLM_REVIEW": "off",
            "SUBMISSIONS_DIR": str(submissions),
            "REPORTS_DIR": str(reports),
            "MOCK_BASE_URL": f"http://127.0.0.1:{port}",
        }
    )
    log_path = Path(work.name) / "mock.log"
    log_file = log_path.open("w", encoding="utf-8")
    mock = subprocess.Popen(
        [
            sys.executable,
            "tools/runtime.py",
            "serve",
            "--data",
            "data/dev",
            "--state",
            state.name,
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=log_file,
        text=True,
    )
    try:
        for _ in range(50):
            if _healthy(port):
                break
            if mock.poll() is not None:
                log_file.flush()
                print(log_path.read_text(encoding="utf-8") or "mock exited", file=sys.stderr)
                return 1
            time.sleep(0.2)
        else:
            print("mock did not answer on 127.0.0.1", file=sys.stderr)
            return 1
        drafted = subprocess.run(
            [sys.executable, "-m", "dispute_copilot", "run-all", "--mock-url", f"http://127.0.0.1:{port}"],
            cwd=ROOT,
            env=env,
        )
        if drafted.returncode != 0:
            return drafted.returncode
        scored = subprocess.run(
            [
                sys.executable,
                "-m",
                "dispute_copilot",
                "evals",
                "--submission",
                str(submissions / "results.json"),
                "--report",
                str(ROOT / "reports" / "evals.json"),
            ],
            cwd=ROOT,
            env=env,
        )
        return scored.returncode
    finally:
        log_file.close()
        mock.terminate()
        try:
            mock.wait(timeout=5)
        except subprocess.TimeoutExpired:
            mock.kill()
        work.cleanup()
        Path(state.name).unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
