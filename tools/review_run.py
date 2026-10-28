"""Serve the review page for the Postgres drafts.

The mock stays on loopback. The page listens on every interface so ingress can open it.
Check does not call a model. This process does not write blobs.
"""

import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOCK_URL = "http://127.0.0.1:8765"
HOST = "0.0.0.0"
PORT = 8080


def require_backends() -> None:
    if (
        os.environ.get("CASES_BACKEND") != "blob"
        or os.environ.get("LEDGER_BACKEND") != "postgres"
        or os.environ.get("REVIEW_BACKEND") != "postgres"
    ):
        raise RuntimeError("backend")


def child_env() -> dict[str, str]:
    require_backends()
    env = dict(os.environ)
    env.pop("POSTGRES_TEST_URL", None)
    env.pop("AUDIT_BACKEND", None)
    env["LLM_PROVIDER"] = "null"
    env["FEATURE_LLM_REVIEW"] = "off"
    env["MOCK_BASE_URL"] = MOCK_URL
    return env


def mock_argv() -> list[str]:
    return [sys.executable, str(ROOT / "tools" / "runtime.py"), "serve", "--port", "8765"]


def listen() -> tuple[str, int]:
    return HOST, PORT


def run_review(start, wait_healthy, serve, stop) -> int:
    env = child_env()
    proc = start(mock_argv(), env)
    try:
        wait_healthy()
        host, port = listen()
        return serve(host, port, env)
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


def _apply(env: dict[str, str]) -> None:
    os.environ.pop("POSTGRES_TEST_URL", None)
    os.environ.pop("AUDIT_BACKEND", None)
    os.environ.update(env)


def serve_page(host: str, port: int, env: dict[str, str]) -> int:
    _apply(env)
    src = ROOT / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import httpx
    import uvicorn

    from dispute_copilot.adapters.case_system import HttpCaseSystem
    from dispute_copilot.adapters.corpus import open_corpus
    from dispute_copilot.adapters.tools import HttpToolClient
    from dispute_copilot.config import Settings
    from dispute_copilot.guardrails import loopback_host
    from dispute_copilot.reliability.retry import load_policy
    from UI.review_page import create_app

    settings = Settings.from_sources(MOCK_URL, None, None)
    if not loopback_host(settings.mock_base_url):
        raise RuntimeError("loopback")
    corpus = open_corpus(settings.data_dir)
    policy = load_policy()
    with httpx.Client(base_url=settings.mock_base_url, timeout=policy.http_timeout_s, trust_env=False) as http:
        tools = HttpToolClient(http, policy=policy)
        ledger = HttpCaseSystem(http)
        uvicorn.run(
            create_app(
                settings.submissions_dir,
                ledger,
                corpus,
                tools,
                state_path=Path(".state") / "run.sqlite",
                reports_dir=settings.reports_dir,
            ),
            host=host,
            port=port,
        )
    return 0


def main() -> int:
    import subprocess

    def start(argv: list[str], env: dict[str, str]):
        return subprocess.Popen(argv, env=env, cwd=ROOT)

    def stop(proc) -> None:
        if proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    try:
        return run_review(start, wait_healthy, serve_page, stop)
    except Exception as exc:
        print(type(exc).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
