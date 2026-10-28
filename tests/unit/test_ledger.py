"""Ledger scenarios. SQLite always runs. Postgres runs only with POSTGRES_TEST_URL."""

import importlib.util
import json
import os
import threading
import time
from http.client import HTTPConnection
from pathlib import Path

import pytest

from dispute_copilot.adapters.ledger import SqliteLedger, open_ledger
from dispute_copilot.serialization import digest, hash_canonical

ROOT = Path(__file__).resolve().parents[2]


def _runtime():
    spec = importlib.util.spec_from_file_location("runtime_ledger", ROOT / "tools" / "runtime.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=["sqlite", "postgres"])
def ledger(request, tmp_path, monkeypatch):
    if request.param == "sqlite":
        store = SqliteLedger(tmp_path / "state.sqlite")
        store.ensure()
        return store
    if not os.environ.get("POSTGRES_TEST_URL"):
        pytest.skip("POSTGRES_TEST_URL is not set")
    pytest.importorskip("psycopg")
    monkeypatch.setenv("LEDGER_BACKEND", "postgres")
    from dispute_copilot.adapters.ledger_postgres import PostgresLedger

    store = PostgresLedger.from_env()
    store.ensure()
    with store.transaction() as tx:
        tx.reset()
    return store


def test_review_pending_and_status(ledger):
    draft = hash_canonical({"case_id": "CARD-D01", "n": 1})
    hashed = digest({"case_id": "CARD-D01", "n": 1})
    with ledger.transaction() as tx:
        tx.insert_review("rev_1", "CARD-D01", draft, hashed, "pending", None)
    with ledger.transaction(immediate=False) as tx:
        pending = tx.list_pending()
    assert [row["id"] for row in pending] == ["rev_1"]
    with ledger.transaction() as tx:
        tx.set_review_status("rev_1", "approved", "Ada")
        row = tx.get_review("rev_1")
        pending = tx.list_pending()
    assert row["status"] == "approved"
    assert row["reviewer"] == "Ada"
    assert row["draft"] == draft
    assert row["hash"] == hashed
    assert pending == []


def test_idempotency_replay_and_conflict(ledger):
    with ledger.transaction() as tx:
        tx.insert_key("key-1", "hash-a", "{\"ok\":true}")
        replay = tx.get_key("key-1")
    assert replay["hash"] == "hash-a"
    assert json.loads(replay["response"]) == {"ok": True}
    with ledger.transaction() as tx:
        previous = tx.get_key("key-1")
        assert previous["hash"] != "hash-b"


def test_duplicate_record_is_visible(ledger):
    with ledger.transaction() as tx:
        assert tx.get_record("logic-1") is None
        tx.insert_record("logic-1", "CARD-D01", "{}", "rev_1")
        existing = tx.get_record("logic-1")
    assert existing["case_id"] == "CARD-D01"
    assert existing["review_id"] == "rev_1"


def test_call_counter_increments(ledger):
    with ledger.transaction() as tx:
        first = tx.bump_call("CARD-D01", "transactions")
        second = tx.bump_call("CARD-D01", "transactions")
    assert first == 1
    assert second == 2


def test_reset_clears_the_ledger(ledger):
    with ledger.transaction() as tx:
        tx.insert_review("rev_1", "CARD-D01", "{}", "h", "pending", None)
        tx.insert_key("key-1", "h", "{}")
        tx.insert_record("logic-1", "CARD-D01", "{}", "rev_1")
        tx.bump_call("CARD-D01", "transactions")
        tx.append_audit("t", "note", "CARD-D01", "{}", {"note": "n"})
        tx.reset()
        assert tx.list_reviews() == []
        assert tx.get_key("key-1") is None
        assert tx.get_record("logic-1") is None
        assert tx.list_audit() == []


def test_default_ledger_is_sqlite(tmp_path):
    store = open_ledger(tmp_path / "state.sqlite")
    assert isinstance(store, SqliteLedger)


def test_postgres_selection_reports_a_missing_driver(monkeypatch, tmp_path):
    import builtins

    monkeypatch.setenv("LEDGER_BACKEND", "postgres")
    monkeypatch.setenv("POSTGRES_HOST", "db.invalid")
    real_import = builtins.__import__

    def blocked(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "psycopg" or name.startswith("psycopg."):
            raise ImportError("no driver")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(RuntimeError, match="cloud extra"):
        open_ledger(tmp_path / "state.sqlite").ensure()


def test_the_mock_writes_a_sqlite_review(tmp_path):
    runtime = _runtime()
    data = tmp_path / "data"
    data.mkdir()
    (data / "dataset.json").write_text(
        json.dumps(
            {
                "name": "tiny",
                "allow_write": True,
                "cases": [
                    {
                        "case_id": "CARD-D01",
                        "intake_source": "in",
                        "documents": [],
                        "logical_key": "logic-1",
                        "tools": {"transactions": {"rows": []}},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    state = tmp_path / "run.sqlite"
    server = runtime.create_server(data, state, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        body = json.dumps({"case_id": "CARD-D01", "draft": {"case_id": "CARD-D01"}})
        response = None
        for _ in range(20):
            try:
                conn = HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("POST", "/v1/reviews", body=body, headers={"Content-Type": "application/json"})
                response = conn.getresponse()
                break
            except (ConnectionRefusedError, OSError):
                time.sleep(0.05)
        assert response is not None
        payload = json.loads(response.read())
        assert response.status == 201
        assert payload["status"] == "pending"
        with SqliteLedger(state).transaction(immediate=False) as tx:
            row = tx.get_review(payload["review_id"])
        assert row["case_id"] == "CARD-D01"
        assert row["status"] == "pending"
    finally:
        server.shutdown()
        server.server_close()
