"""The cloud check lists stores. It does not wipe them."""

import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location("cloud_check", ROOT / "tools" / "cloud_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Ledger:
    def __init__(self) -> None:
        self.ensured = False
        self.reset_called = False
        self.clear_called = False

    def ensure(self) -> None:
        self.ensured = True

    def reset(self) -> None:
        self.reset_called = True

    def clear(self) -> None:
        self.clear_called = True


def test_check_lists_prefixes_and_does_not_wipe(capsys):
    module = _load()
    ledger = Ledger()
    seen = []

    def query():
        return {"ok": 1}

    def blobs(kind: str, prefix: str):
        seen.append((kind, prefix))

    module.check(ledger, query, blobs)
    assert ledger.ensured is True
    assert ledger.reset_called is False
    assert ledger.clear_called is False
    assert seen == [("cases", "documents/"), ("cases", "policies/"), ("audit", "audit/")]
    assert capsys.readouterr().out.splitlines() == ["postgres ok", "cases list ok", "audit list ok"]


def test_a_failed_query_does_not_list_or_wipe():
    module = _load()
    ledger = Ledger()
    seen = []

    def query():
        return None

    def blobs(kind: str, prefix: str):
        seen.append((kind, prefix))

    with pytest.raises(RuntimeError):
        module.check(ledger, query, blobs)
    assert seen == []
    assert ledger.reset_called is False
    assert ledger.clear_called is False


def test_the_check_source_does_not_wipe():
    text = (ROOT / "tools" / "cloud_check.py").read_text(encoding="utf-8")
    assert ".reset(" not in text
    assert ".clear(" not in text


def test_drop_test_url_forgets_the_disposable_database(monkeypatch):
    monkeypatch.setenv("POSTGRES_TEST_URL", "postgresql://example")
    _load().drop_test_url()
    assert "POSTGRES_TEST_URL" not in os.environ


def test_main_prints_the_error_type_only(monkeypatch, capsys):
    module = _load()

    def boom():
        raise RuntimeError("postgresql://secret")

    monkeypatch.setattr(module, "live_check", boom)
    assert module.main() == 1
    captured = capsys.readouterr()
    assert captured.err.strip() == "RuntimeError"
    assert "secret" not in captured.err
    assert captured.out == ""
