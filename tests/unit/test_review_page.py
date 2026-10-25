import json
from pathlib import Path

from fastapi.testclient import TestClient

from dispute_copilot.adapters.case_system import FakeCaseSystem, ReviewRecord
from dispute_copilot.review_page import create_app
from dispute_copilot.serialization import digest, hash_canonical
from dispute_copilot.storage import write_draft

CUSTOMER = (
    "We have prepared your complaint for review. "
    "We cannot confirm an outcome. "
    "No case has been created yet; human approval remains required."
)
QUOTE = "Use unauthorized when the cardholder did not allow the purchase."


def _draft(*, amount: int | None = None) -> dict:
    return {
        "case_id": "CARD-D01",
        "classification": "unauthorized",
        "routing": "ready_for_review",
        "policy_ids": ["CARD-POLICY-V2", "CARD-PROCEDURE-01"],
        "facts": {
            "transaction_id": "TX-D01",
            "amount": amount,
            "currency": "XCR",
            "reported_date": "2026-10-01",
        },
        "missing_information": ["authorization_confirmation"],
        "deadline": None,
        "citations": [
            {
                "claim": "classification",
                "source_id": "CARD-PROCEDURE-01",
                "quote": QUOTE,
            }
        ],
        "follow_up_questions": [
            {
                "field": "authorization_confirmation",
                "question": "Please confirm whether this purchase was authorized.",
            }
        ],
        "narrative": f"Case summary: unauthorized complaint for TX-D01. Customer message draft: {CUSTOMER}",
    }


def _approval(draft_hash: str) -> dict:
    return {
        "case_id": "CARD-D01",
        "draft_version": 1,
        "draft_hash": draft_hash,
        "passed": True,
        "checks": {"contract_valid": True},
        "run_id": "test",
        "llm_review": None,
        "contract_version": "1.0",
    }


def _routes(app) -> list[tuple[str, str]]:
    found = []

    def walk(routes) -> None:
        for route in routes:
            found.append((getattr(route, "path", "") or "", getattr(route, "name", "") or ""))
            nested = getattr(route, "routes", None)
            if nested:
                walk(nested)

    walk(app.routes)
    return found


def test_a_saved_draft_renders_the_contract_sections(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FEATURE_LLM_REVIEW", "off")
    write_draft(tmp_path, "CARD-D01", _draft())
    client = TestClient(create_app(tmp_path, FakeCaseSystem()))
    response = client.get("/cases/CARD-D01")
    assert response.status_code == 200
    text = response.text
    assert "CARD-D01" in text
    assert "unauthorized" in text
    assert "ready_for_review" in text
    assert "CARD-POLICY-V2" in text
    assert "TX-D01" in text
    assert "null" in text
    assert "authorization_confirmation" in text
    assert "Please confirm whether this purchase was authorized." in text
    assert QUOTE in text
    assert "CARD-PROCEDURE-01" in text
    assert CUSTOMER in text
    assert "Draft" in text
    assert "No check yet" in text
    assert "Review not submitted" in text
    assert text.index("Judge note") < text.index("Type")
    assert "No judge note yet." in text


def test_the_judge_note_is_above_the_decision(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FEATURE_LLM_REVIEW", "off")
    saved = _draft()
    write_draft(tmp_path, "CARD-D01", saved)
    (tmp_path / "reviews").mkdir()
    (tmp_path / "reviews" / "CARD-D01.json").write_text(
        json.dumps(
            {
                "agent_approval": {
                    "case_id": "CARD-D01",
                    "draft_hash": digest(saved),
                    "passed": True,
                    "checks": {"contract_valid": True},
                    "llm_review": {
                        "verdict": "pass",
                        "reason": "The draft names the purchase and does not promise an outcome.",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    text = TestClient(create_app(tmp_path, FakeCaseSystem())).get("/cases/CARD-D01").text
    assert text.index("Judge note") < text.index("Type")
    assert "The draft names the purchase and does not promise an outcome." in text


def test_the_page_has_no_approve_control(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FEATURE_LLM_REVIEW", "off")
    write_draft(tmp_path, "CARD-D01", _draft())
    app = create_app(tmp_path, FakeCaseSystem())
    for path, name in _routes(app):
        assert "approve" not in path
        assert "approve" not in name
    text = TestClient(app).get("/cases/CARD-D01").text.lower()
    for line in text.splitlines():
        if "<button" in line or "<form" in line:
            assert "approve" not in line


def test_submit_without_a_passed_check_is_refused(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FEATURE_LLM_REVIEW", "off")
    write_draft(tmp_path, "CARD-D01", _draft())
    ledger = FakeCaseSystem()
    client = TestClient(create_app(tmp_path, ledger))
    response = client.post("/cases/CARD-D01/submit", follow_redirects=True)
    assert response.status_code == 200
    assert "agent_approval_required" in response.text
    assert ledger.submitted == []


def test_create_while_the_review_is_pending_is_refused(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FEATURE_LLM_REVIEW", "off")
    saved = _draft(amount=117)
    write_draft(tmp_path, "CARD-D01", saved)
    raw = hash_canonical(saved)
    ledger = FakeCaseSystem()
    ledger.reviews["rev_1"] = ReviewRecord("rev_1", "CARD-D01", "pending", digest(saved), raw, None)
    (tmp_path / "reviews").mkdir()
    (tmp_path / "reviews" / "CARD-D01.json").write_text(
        json.dumps(
            {
                "review_id": "rev_1",
                "draft_hash": digest(saved),
                "agent_approval": _approval(digest(saved)),
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(create_app(tmp_path, ledger))
    response = client.post("/cases/CARD-D01/create", follow_redirects=True)
    assert response.status_code == 200
    assert "approval_gate" in response.text
    assert ledger.posts == []


def test_a_restricted_draft_is_not_rendered(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FEATURE_LLM_REVIEW", "off")
    draft = _draft()
    draft["narrative"] = "Customer message draft: " + "SYNTHETIC-" + "PRIVATE"
    write_draft(tmp_path, "CARD-D01", draft)
    response = TestClient(create_app(tmp_path, FakeCaseSystem())).get("/cases/CARD-D01")
    assert response.status_code == 403
    assert "This draft cannot be shown." in response.text
    assert "SYNTHETIC-" + "PRIVATE" not in response.text
    assert QUOTE not in response.text
