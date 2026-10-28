import json
from pathlib import Path

from dispute_copilot.adapters.case_system import FakeCaseSystem, ReviewRecord
from dispute_copilot.errors import CreateRejected
from dispute_copilot.review import create_record, load_handle, submit_review
from dispute_copilot.review_flow import agent_approval_matches
from dispute_copilot.review_flow.handles import handle_path, read_handle, write_handle
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
    assert "verdict" not in ledger.submitted[0]
    assert "llm_review" not in ledger.submitted[0]
    assert handle["review_id"] == "rev_1"
    assert json.loads((tmp_path / "reviews" / "CARD-D01.json").read_text(encoding="utf-8"))["draft_version"] == 1


def test_submit_posts_without_agent_approval(tmp_path: Path):
    saved = _draft("LOCAL")
    write_draft(tmp_path, "CARD-D01", saved)
    ledger = FakeCaseSystem()
    handle = submit_review("CARD-D01", tmp_path, ledger)
    assert ledger.submitted == [saved]
    assert handle["review_id"] == "rev_1"
    assert handle.get("agent_approval") is None


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


def test_load_handle_and_create_reject_unusable_files(tmp_path: Path):
    import pytest

    path = handle_path(tmp_path, "CARD-D01")
    with pytest.raises(CreateRejected) as missing:
        load_handle(path)
    assert missing.value.payload["error"] == "review_not_submitted"
    write_handle(path, {"draft_hash": "abc"})
    with pytest.raises(CreateRejected) as unusable:
        load_handle(path)
    assert unusable.value.payload["error"] == "review_handle_unusable"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        read_handle(path)
    write_handle(path, {"review_id": "rev_1", "draft_hash": "abc"})
    ledger = FakeCaseSystem()
    ledger.reviews["rev_1"] = ReviewRecord("rev_1", "CARD-D01", "approved", "hash", "not-json", "person")
    with pytest.raises(CreateRejected) as bad_json:
        create_record("CARD-D01", tmp_path, ledger)
    assert bad_json.value.payload["error"] == "draft_not_json"
    ledger.reviews["rev_1"] = ReviewRecord("rev_1", "CARD-D01", "approved", "hash", '{"case_id":"OTHER"}', "person")
    with pytest.raises(CreateRejected) as mismatch:
        create_record("CARD-D01", tmp_path, ledger)
    assert mismatch.value.payload["error"] == "draft_case_mismatch"


def test_agent_approval_must_name_the_same_case_and_hash():
    saved = _draft("LOCAL")
    hashed = digest(saved)
    assert agent_approval_matches({"agent_approval": {"passed": False}}, "CARD-D01", saved) is False
    assert (
        agent_approval_matches(
            {"agent_approval": {"passed": True, "case_id": "CARD-D02", "draft_hash": hashed}},
            "CARD-D01",
            saved,
        )
        is False
    )
    assert (
        agent_approval_matches(
            {"agent_approval": {"passed": True, "case_id": "CARD-D01", "draft_hash": hashed}},
            "CARD-D01",
            saved,
        )
        is True
    )


def test_create_posts_after_human_approval_without_agent_approval(tmp_path: Path):
    server = _draft("FROM-SERVER")
    raw = hash_canonical(server)
    ledger = FakeCaseSystem()
    ledger.reviews["rev_1"] = ReviewRecord("rev_1", "CARD-D01", "approved", digest(server), raw, "person")
    (tmp_path / "reviews").mkdir()
    (tmp_path / "reviews" / "CARD-D01.json").write_text(
        json.dumps({"review_id": "rev_1", "draft_hash": digest(server)}),
        encoding="utf-8",
    )
    first = create_record("CARD-D01", tmp_path, ledger)
    assert first["posted"] is True
    assert ledger.posts[0]["draft"]["marker"] == "FROM-SERVER"


def test_create_refuses_when_the_server_hash_does_not_match_the_body(tmp_path: Path):
    server = _draft("FROM-SERVER")
    raw = hash_canonical(server)
    ledger = FakeCaseSystem()
    ledger.reviews["rev_1"] = ReviewRecord("rev_1", "CARD-D01", "approved", "deadbeef", raw, "Ada")
    (tmp_path / "reviews").mkdir()
    (tmp_path / "reviews" / "CARD-D01.json").write_text(
        json.dumps(
            {
                "review_id": "rev_1",
                "draft_hash": "deadbeef",
                "agent_approval": _approval("deadbeef"),
            }
        ),
        encoding="utf-8",
    )
    try:
        create_record("CARD-D01", tmp_path, ledger)
    except CreateRejected as exc:
        assert exc.payload["error"] == "hash_mismatch"
    else:
        raise AssertionError("a hash mismatch was accepted")
