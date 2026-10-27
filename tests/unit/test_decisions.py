import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from dispute_copilot.adapters.case_system import FakeCaseSystem
from dispute_copilot.serialization import digest, hash_canonical
from UI.decisions import open_ledger, record_decision
from UI.review_page import create_app


def _seed(state: Path, *, status: str = "pending") -> tuple[str, str]:
    draft = {
        "case_id": "CARD-D01",
        "narrative": "Customer message draft: We have prepared your complaint for review.",
    }
    stored = hash_canonical(draft)
    hashed = digest(draft)
    with open_ledger(state) as db:
        db.execute(
            "INSERT INTO reviews VALUES(?,?,?,?,?,?)",
            ("rev_page_1", "CARD-D01", stored, hashed, status, None),
        )
        db.execute(
            "INSERT INTO keys VALUES(?,?,?)",
            ("dc-existing", "fingerprint", json.dumps({"record_id": "kept"})),
        )
    return stored, hashed


def test_accept_changes_status_only(tmp_path: Path):
    state = tmp_path / "run.sqlite"
    stored, hashed = _seed(state)
    assert record_decision(state, "rev_page_1", "Ada", accepted=True) == "approved"
    with open_ledger(state) as db:
        row = db.execute("SELECT * FROM reviews WHERE id='rev_page_1'").fetchone()
        key = db.execute("SELECT * FROM keys").fetchone()
        event = db.execute("SELECT event, detail FROM audit").fetchone()
    assert row["status"] == "approved"
    assert row["reviewer"] == "Ada"
    assert row["draft"] == stored
    assert row["hash"] == hashed
    assert key["key"] == "dc-existing"
    detail = json.loads(event["detail"])
    assert event["event"] == "human_approved"
    assert detail["draft_hash"] == hashed
    assert detail["reviewer"] == "Ada"


def test_decline_and_a_second_decision_leave_the_hash(tmp_path: Path):
    state = tmp_path / "run.sqlite"
    stored, hashed = _seed(state)
    assert record_decision(state, "rev_page_1", "Ada", accepted=False) == "rejected"
    try:
        record_decision(state, "rev_page_1", "Bea", accepted=True)
    except Exception as exc:
        assert exc.code == "pending"
    else:
        raise AssertionError("a decided review was changed")
    with open_ledger(state) as db:
        row = db.execute("SELECT status, reviewer, draft, hash FROM reviews").fetchone()
    assert row["status"] == "rejected"
    assert row["reviewer"] == "Ada"
    assert row["draft"] == stored
    assert row["hash"] == hashed


def test_a_blank_name_does_not_write(tmp_path: Path):
    state = tmp_path / "run.sqlite"
    _seed(state)
    try:
        record_decision(state, "rev_page_1", "  ", accepted=True)
    except Exception as exc:
        assert exc.code == "name"
    else:
        raise AssertionError("a blank name was stored")
    with open_ledger(state) as db:
        row = db.execute("SELECT status, reviewer FROM reviews").fetchone()
        audits = db.execute("SELECT COUNT(*) AS n FROM audit").fetchone()["n"]
    assert row["status"] == "pending"
    assert row["reviewer"] is None
    assert audits == 0


def test_the_decisions_page_accepts_without_a_copied_id(tmp_path: Path):
    state = tmp_path / "run.sqlite"
    stored, hashed = _seed(state)
    client = TestClient(create_app(tmp_path, FakeCaseSystem(), state_path=state))
    page = client.get("/decisions")
    assert page.status_code == 200
    assert "rev_page_1" in page.text
    assert 'action="/decisions/rev_page_1/accept"' in page.text
    assert "We have prepared your complaint for review." in page.text
    decided = client.post("/decisions/rev_page_1/accept", data={"reviewer": "Ada"}, follow_redirects=True)
    assert decided.status_code == 200
    assert "Accepted." in decided.text
    with open_ledger(state) as db:
        row = db.execute("SELECT status, reviewer, draft, hash FROM reviews").fetchone()
    assert row["status"] == "approved"
    assert row["reviewer"] == "Ada"
    assert row["draft"] == stored
    assert row["hash"] == hashed
    missing = client.post("/decisions/rev_page_1/decline", data={"reviewer": "Bea"}, follow_redirects=True)
    assert "not pending" in missing.text
    with open_ledger(state) as db:
        again = db.execute("SELECT status, reviewer, draft, hash FROM reviews").fetchone()
    assert again["status"] == "approved"
    assert again["reviewer"] == "Ada"
    assert again["draft"] == stored
    assert again["hash"] == hashed
    case = client.get("/decisions")
    assert "Approved" in case.text


def test_the_case_page_links_to_decisions(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FEATURE_LLM_REVIEW", "off")
    from dispute_copilot.storage import write_draft

    write_draft(
        tmp_path,
        "CARD-D01",
        {
            "case_id": "CARD-D01",
            "classification": "unauthorized",
            "routing": "ready_for_review",
            "policy_ids": [],
            "facts": {},
            "missing_information": [],
            "deadline": None,
            "citations": [],
            "follow_up_questions": [],
            "narrative": "Customer message draft: Ready for a person.",
        },
    )
    text = TestClient(create_app(tmp_path, FakeCaseSystem())).get("/cases/CARD-D01").text
    assert 'href="/decisions"' in text
    assert "operator command" not in text
    assert "Create record" not in text


def test_an_accepted_review_can_create_a_record_on_the_decisions_page(tmp_path: Path):
    state = tmp_path / "state" / "run.sqlite"
    _seed(state, status="approved")
    client = TestClient(create_app(tmp_path, FakeCaseSystem(), state_path=state))
    page = client.get("/decisions")
    assert page.status_code == 200
    assert 'action="/decisions/CARD-D01/record"' in page.text
    assert "Create record" in page.text
    response = client.post("/decisions/CARD-D01/record", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/decisions?notice=")


def test_sqlite_schema_matches_the_mock(tmp_path: Path):
    state = tmp_path / "run.sqlite"
    with open_ledger(state) as db:
        columns = [row[1] for row in db.execute("PRAGMA table_info(reviews)")]
    assert columns == ["id", "case_id", "draft", "hash", "status", "reviewer"]
    assert sqlite3.connect(state).execute("SELECT COUNT(*) FROM keys").fetchone()[0] == 0
