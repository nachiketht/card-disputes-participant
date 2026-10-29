"""Approach 3 drafts through the rankers. It does not wipe storage or print a complaint."""

import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location("approach3_run", ROOT / "tools" / "approach3_run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ready(monkeypatch):
    monkeypatch.setenv("CASES_BACKEND", "blob")
    monkeypatch.setenv("LEDGER_BACKEND", "postgres")
    monkeypatch.setenv("POSTGRES_TEST_URL", "postgresql://secret")
    monkeypatch.setenv("LLM_PROVIDER", "null")


def test_approach_3_drops_the_provider_and_leaves_the_judge_off(monkeypatch):
    _ready(monkeypatch)
    module = _load()
    started = []
    stopped = []
    reported = []

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

    assert module.run_approach3(start, wait_healthy, run_copilot, stop, lambda: reported.append("paths")) == 0
    mock_argv, mock_env = started[0]
    copilot_argv, copilot_env = started[1]
    assert copilot_argv == ["dispute-copilot", "run-all", "--approach", "3"]
    assert "reset" not in mock_argv
    assert "reset" not in copilot_argv
    assert "LLM_PROVIDER" not in mock_env
    assert "LLM_PROVIDER" not in copilot_env
    assert mock_env["FEATURE_LLM_REVIEW"] == "off"
    assert copilot_env["FEATURE_LLM_REVIEW"] == "off"
    assert mock_env["REVIEW_BACKEND"] == "postgres"
    assert "POSTGRES_TEST_URL" not in mock_env
    assert "secret" not in " ".join(copilot_argv)
    assert reported == ["paths"]
    assert stopped == [{"id": "mock"}]


def test_a_failed_copilot_does_not_print_paths(monkeypatch):
    _ready(monkeypatch)
    module = _load()

    def start(argv, env):
        return "mock"

    def run_copilot(argv, env):
        return 1

    reported = []
    assert module.run_approach3(start, lambda: None, run_copilot, lambda proc: None, lambda: reported.append("paths")) == 1
    assert reported == []


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
        module.run_approach3(start, wait_healthy, run_copilot, stop, lambda: None)
    assert stopped == ["mock"]


def test_missing_backends_do_not_start(monkeypatch):
    monkeypatch.delenv("CASES_BACKEND", raising=False)
    monkeypatch.delenv("LEDGER_BACKEND", raising=False)
    module = _load()

    def start(argv, env):
        raise AssertionError("started")

    with pytest.raises(RuntimeError):
        module.run_approach3(start, lambda: None, lambda argv, env: 0, lambda proc: None, lambda: None)


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


def test_path_lines_omit_a_planted_complaint(tmp_path: Path, capsys):
    module = _load()
    complaint = "I found a card purchase at Morrow Supplies"
    (tmp_path / "CARD-D01.json").write_text(
        json.dumps(
            {
                "case_id": "CARD-D01",
                "path": "jev_agreed",
                "final_class": "unauthorized",
                "route": "ready_for_review",
                "note": complaint,
            }
        ),
        encoding="utf-8",
    )
    module.print_paths(tmp_path)
    captured = capsys.readouterr()
    assert captured.out.strip() == "CARD-D01 jev_agreed unauthorized ready_for_review"
    assert complaint not in captured.out
    assert complaint not in captured.err
