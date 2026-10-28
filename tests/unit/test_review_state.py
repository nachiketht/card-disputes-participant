"""Postgres review drafts, results, and handles. Skipped unless POSTGRES_TEST_URL is set."""

import os
from pathlib import Path

import pytest

from dispute_copilot.storage import write_draft


def test_files_stay_the_default(tmp_path: Path):
    path = write_draft(tmp_path, "CARD-D01", {"case_id": "CARD-D01", "n": 1})
    assert path.is_file()
    assert os.environ.get("REVIEW_BACKEND") != "postgres"


def test_postgres_review_round_trip():
    if not os.environ.get("POSTGRES_TEST_URL"):
        pytest.skip("POSTGRES_TEST_URL is not set")
    pytest.importorskip("psycopg")
    from dispute_copilot.adapters.review_state import PostgresReviewStore

    store = PostgresReviewStore.from_env()
    store.clear()
    first = store.write_draft("CARD-D01", {"case_id": "CARD-D01", "n": 1})
    second = store.write_draft("CARD-D01", {"case_id": "CARD-D01", "n": 2})
    store.write_draft("CARD-D02", {"case_id": "CARD-D02", "n": 1})
    draft, version = store.latest("CARD-D01")
    assert first == 1
    assert second == 2
    assert version == 2
    assert draft == {"case_id": "CARD-D01", "n": 2}
    assert store.result_for("CARD-D01") == {"case_id": "CARD-D01", "n": 2}
    assert store.result_for("CARD-D02") == {"case_id": "CARD-D02", "n": 1}
    assert store.read_handle("CARD-D01") == {}
    store.write_handle("CARD-D01", {"review_id": "rev_1", "draft_hash": "abc"})
    assert store.read_handle("CARD-D01")["review_id"] == "rev_1"
    assert store.draft_case_ids() == ["CARD-D01", "CARD-D02"]
    with pytest.raises(FileNotFoundError):
        store.latest("CARD-MISSING")
    store.write_draft("CARD-OPEN", {"case_id": "CARD-OPEN", "classification": "unauthorized"})
    store.write_draft("CARD-CREATED", {"case_id": "CARD-CREATED", "classification": "duplicate_charge"})
    store.write_draft("CARD-DECLINED", {"case_id": "CARD-DECLINED", "classification": "unauthorized"})
    listed = {row["id"]: row["type_value"] for row in store.open_cases()}
    assert listed["CARD-OPEN"] == "unauthorized"
    assert listed["CARD-CREATED"] == "duplicate_charge"
    assert listed["CARD-D01"] == ""
    conn = store._conn()
    try:
        conn.execute(
            "INSERT INTO records (id, case_id, draft, review_id) VALUES (%s, %s, %s, %s)",
            ("rec-created", "CARD-CREATED", "{}", "rev-created"),
        )
        conn.execute(
            "INSERT INTO reviews (id, case_id, draft, hash, status, reviewer) VALUES (%s, %s, %s, %s, %s, %s)",
            ("rev-declined", "CARD-DECLINED", "{}", "hash", "rejected", "Ada"),
        )
        conn.commit()
    finally:
        conn.close()
    try:
        left = {row["id"] for row in store.open_cases()}
        assert "CARD-CREATED" not in left
        assert "CARD-DECLINED" not in left
        assert "CARD-OPEN" in left
    finally:
        conn = store._conn()
        try:
            conn.execute("DELETE FROM records WHERE id=%s", ("rec-created",))
            conn.execute("DELETE FROM reviews WHERE id=%s", ("rev-declined",))
            conn.commit()
        finally:
            conn.close()
    store.clear()
