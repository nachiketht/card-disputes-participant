"""The review process listens on every interface. The local command stays on loopback."""

import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location("review_run", ROOT / "tools" / "review_run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ready(monkeypatch):
    monkeypatch.setenv("CASES_BACKEND", "blob")
    monkeypatch.setenv("LEDGER_BACKEND", "postgres")
    monkeypatch.setenv("REVIEW_BACKEND", "postgres")
    monkeypatch.setenv("POSTGRES_TEST_URL", "postgresql://secret")
    monkeypatch.setenv("AUDIT_BACKEND", "blob")


def test_review_listens_on_every_interface_and_leaves_the_model_off(monkeypatch):
    _ready(monkeypatch)
    module = _load()
    started = []
    served = []
    stopped = []

    def start(argv, env):
        started.append((argv, env))
        return {"id": "mock"}

    def serve(host, port, env):
        served.append((host, port, env))
        return 0

    def stop(proc):
        stopped.append(proc)

    assert module.run_review(start, lambda: None, serve, stop) == 0
    argv, env = started[0]
    assert argv[1].endswith("tools/runtime.py")
    assert "serve" in argv
    assert "8765" in argv
    assert "reset" not in argv
    assert served[0][0] == "0.0.0.0"
    assert served[0][1] == 8080
    assert env["LLM_PROVIDER"] == "null"
    assert env["FEATURE_LLM_REVIEW"] == "off"
    assert env["MOCK_BASE_URL"] == "http://127.0.0.1:8765"
    assert "POSTGRES_TEST_URL" not in env
    assert "AUDIT_BACKEND" not in env
    assert "secret" not in " ".join(argv)
    assert stopped == [{"id": "mock"}]


def test_a_failed_health_check_still_stops_the_mock(monkeypatch):
    _ready(monkeypatch)
    module = _load()
    stopped = []

    def wait_healthy():
        raise RuntimeError("health")

    def serve(host, port, env):
        raise AssertionError("page started")

    def stop(proc):
        stopped.append(proc)

    with pytest.raises(RuntimeError):
        module.run_review(lambda argv, env: "mock", wait_healthy, serve, stop)
    assert stopped == ["mock"]


def test_missing_review_backend_does_not_start(monkeypatch):
    monkeypatch.setenv("CASES_BACKEND", "blob")
    monkeypatch.setenv("LEDGER_BACKEND", "postgres")
    monkeypatch.delenv("REVIEW_BACKEND", raising=False)
    module = _load()

    def start(argv, env):
        raise AssertionError("started")

    with pytest.raises(RuntimeError):
        module.run_review(start, lambda: None, lambda host, port, env: 0, lambda proc: None)


def test_the_local_command_stays_on_loopback():
    text = (ROOT / "src" / "dispute_copilot" / "cli.py").read_text(encoding="utf-8")
    assert 'host="127.0.0.1"' in text


def test_main_prints_the_error_type_only(monkeypatch, capsys):
    monkeypatch.delenv("CASES_BACKEND", raising=False)
    monkeypatch.setenv("LEDGER_BACKEND", "postgres")
    monkeypatch.setenv("REVIEW_BACKEND", "postgres")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    module = _load()
    assert module.main() == 1
    captured = capsys.readouterr()
    assert captured.err.strip() == "RuntimeError"
    assert "secret" not in captured.err
    assert captured.out == ""
