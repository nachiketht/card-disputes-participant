"""The batch command drafts with approach 1. It does not call a model or wipe storage."""

import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location("batch_run", ROOT / "tools" / "batch_run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ready(monkeypatch):
    monkeypatch.setenv("CASES_BACKEND", "blob")
    monkeypatch.setenv("LEDGER_BACKEND", "postgres")
    monkeypatch.setenv("POSTGRES_TEST_URL", "postgresql://secret")


def test_batch_uses_approach_1_and_leaves_the_model_off(monkeypatch):
    _ready(monkeypatch)
    module = _load()
    started = []
    stopped = []

    def start(argv, env):
        started.append((argv, env))
        return {"id": "mock"}

    def wait_healthy():
        return None

    def run_copilot(argv, env):
        started.append((argv, env))
        return 0

    def stop(proc):
        stopped.append(proc)

    assert module.run_batch(start, wait_healthy, run_copilot, stop) == 0
    mock_argv, mock_env = started[0]
    copilot_argv, copilot_env = started[1]
    assert copilot_argv == ["dispute-copilot", "run-all", "--approach", "1"]
    assert "reset" not in mock_argv
    assert "reset" not in copilot_argv
    assert mock_env["LLM_PROVIDER"] == "null"
    assert mock_env["FEATURE_LLM_REVIEW"] == "off"
    assert mock_env["REVIEW_BACKEND"] == "postgres"
    assert copilot_env["LLM_PROVIDER"] == "null"
    assert copilot_env["FEATURE_LLM_REVIEW"] == "off"
    assert "POSTGRES_TEST_URL" not in mock_env
    assert "secret" not in " ".join(copilot_argv)
    assert stopped == [{"id": "mock"}]


def test_a_failed_health_check_still_stops_the_mock(monkeypatch):
    _ready(monkeypatch)
    module = _load()
    stopped = []

    def start(argv, env):
        return "mock"

    def wait_healthy():
        raise RuntimeError("health")

    def run_copilot(argv, env):
        raise AssertionError("copilot ran")

    def stop(proc):
        stopped.append(proc)

    with pytest.raises(RuntimeError):
        module.run_batch(start, wait_healthy, run_copilot, stop)
    assert stopped == ["mock"]


def test_missing_backends_do_not_start(monkeypatch):
    monkeypatch.delenv("CASES_BACKEND", raising=False)
    monkeypatch.delenv("LEDGER_BACKEND", raising=False)
    module = _load()

    def start(argv, env):
        raise AssertionError("started")

    with pytest.raises(RuntimeError):
        module.run_batch(start, lambda: None, lambda argv, env: 0, lambda proc: None)


def test_main_prints_the_error_type_only(monkeypatch, capsys):
    monkeypatch.delenv("CASES_BACKEND", raising=False)
    monkeypatch.setenv("LEDGER_BACKEND", "postgres")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    module = _load()
    assert module.main() == 1
    captured = capsys.readouterr()
    assert captured.err.strip() == "RuntimeError"
    assert "secret" not in captured.err
    assert captured.out == ""
