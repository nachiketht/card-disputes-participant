import json
from pathlib import Path

from dispute_copilot.adapters.case_system import FakeCaseSystem, ReviewRecord
from dispute_copilot.errors import CreateRejected
from dispute_copilot.review import create_record, submit_review
from dispute_copilot.serialization import digest, hash_canonical
from dispute_copilot.storage import write_draft


def _draft(marker):
    return {
        "case_id": "CARD-D01",
        "classification": "unauthorized",
        "routing": "ready_for_review",
        "marker": marker,
    }


def _approval(draft_hash: str) -> dict:
    return {
        "case_id": "CARD-D01",
        "draft_version": 1,
        "draft_hash": draft_hash,
        "passed": True,
        "checks": {},
        "run_id": "test",
        "llm_review": None,
        "contract_version": "1.0",
    }


def test_submit_posts_the_saved_draft(tmp_path: Path):
    saved = _draft("LOCAL")
    write_draft(tmp_path, "CARD-D01", saved)
    (tmp_path / "reviews").mkdir()
    (tmp_path / "reviews" / "CARD-D01.json").write_text(
        json.dumps({"agent_approval": _approval(digest(saved))}),
        encoding="utf-8",
    )
    ledger = FakeCaseSystem()
    handle = submit_review("CARD-D01", tmp_path, ledger)
    assert ledger.submitted == [saved]
    assert handle["review_id"] == "rev_1"
    assert json.loads((tmp_path / "reviews" / "CARD-D01.json").read_text(encoding="utf-8"))["draft_version"] == 1


def test_submit_refuses_without_a_passed_agent_approval(tmp_path: Path):
    write_draft(tmp_path, "CARD-D01", _draft("LOCAL"))
    ledger = FakeCaseSystem()
    try:
        submit_review("CARD-D01", tmp_path, ledger)
    except CreateRejected as exc:
        assert exc.payload["error"] == "agent_approval_required"
    else:
        raise AssertionError("submit posted without a step-1 approval")
    assert ledger.submitted == []


def test_create_posts_the_server_draft_only_after_approval(tmp_path: Path):
    server = _draft("FROM-SERVER")
    raw = hash_canonical(server)
    ledger = FakeCaseSystem()
    ledger.reviews["rev_1"] = ReviewRecord("rev_1", "CARD-D01", "pending", digest(server), raw, None)
    (tmp_path / "reviews").mkdir()
    (tmp_path / "reviews" / "CARD-D01.json").write_text(
        json.dumps(
            {
                "review_id": "rev_1",
                "draft_hash": digest(server),
                "agent_approval": _approval(digest(server)),
            }
        ),
        encoding="utf-8",
    )
    try:
        create_record("CARD-D01", tmp_path, ledger)
    except CreateRejected:
        pass
    else:
        raise AssertionError("a pending review posted a record")
    assert ledger.posts == []

    ledger.reviews["rev_1"] = ReviewRecord("rev_1", "CARD-D01", "approved", "not-the-hash", raw, "person")
    try:
        create_record("CARD-D01", tmp_path, ledger)
    except CreateRejected:
        pass
    assert ledger.posts == []

    ledger.reviews["rev_1"] = ReviewRecord("rev_1", "CARD-D01", "approved", digest(server), raw, "person")
    first = create_record("CARD-D01", tmp_path, ledger)
    assert first["posted"] is True
    assert ledger.posts[0]["draft"]["marker"] == "FROM-SERVER"
    second = create_record("CARD-D01", tmp_path, ledger)
    assert second["posted"] is False
    assert second["record_id"] == first["record_id"]
    assert len(ledger.posts) == 1


def test_an_existing_record_is_reported_without_a_post(tmp_path: Path):
    ledger = FakeCaseSystem()
    ledger.records["CARD-D01"] = {"id": "already-there", "case_id": "CARD-D01"}
    (tmp_path / "reviews").mkdir()
    (tmp_path / "reviews" / "CARD-D01.json").write_text(
        json.dumps({"review_id": "rev_1", "draft_hash": "abc"}),
        encoding="utf-8",
    )
    outcome = create_record("CARD-D01", tmp_path, ledger)
    assert outcome["posted"] is False
    assert outcome["record_id"] == "already-there"
    assert ledger.posts == []
