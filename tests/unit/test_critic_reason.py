"""The step-1 judge stores a pass or block and a short paragraph reason."""

import json
from pathlib import Path

from dispute_copilot.llm.base import Completion, LLMError
from dispute_copilot.review_flow.agent_review import format_reviewer_note, record_agent_review
from dispute_copilot.review_flow.handles import handle_path


def _draft() -> dict:
    return {
        "case_id": "CARD-D01",
        "classification": "unauthorized",
        "routing": "ready_for_review",
        "policy_ids": ["CARD-POLICY-V2"],
        "facts": {
            "transaction_id": "TX-D01",
            "amount": 117,
            "currency": "XCR",
            "reported_date": "2026-10-01",
        },
        "missing_information": [],
        "deadline": "2026-10-31",
        "citations": [
            {
                "claim": "rule",
                "source_id": "CARD-PROCEDURE-01",
                "quote": "Use unauthorized when the customer denies the purchase.",
            }
        ],
        "follow_up_questions": [],
        "narrative": (
            "Customer message draft: We have prepared your complaint for review. "
            "We cannot confirm an outcome. No case has been created yet; human approval remains required."
        ),
    }


class _Scripted:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def complete(self, *, system: str, user: str) -> Completion:
        del system, user
        self.calls += 1
        return Completion(self.text, 0, 0, "fake")


class _Turns:
    def __init__(self, *texts: str) -> None:
        self.texts = list(texts)
        self.calls = 0

    def complete(self, *, system: str, user: str) -> Completion:
        del system, user
        index = min(self.calls, len(self.texts) - 1)
        self.calls += 1
        return Completion(self.texts[index], 0, 0, "fake")


class _Raising:
    def complete(self, *, system: str, user: str) -> Completion:
        del system, user
        raise LLMError("provider timeout")


def _saved(tmp_path: Path) -> dict:
    return json.loads(handle_path(tmp_path, "CARD-D01").read_text(encoding="utf-8"))["agent_approval"]


def test_pass_stores_the_reason_and_leaves_the_draft(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FEATURE_LLM_REVIEW", "block")
    draft = _draft()
    before = json.dumps(draft)
    reason = (
        "This is an unauthorized complaint for purchase TX-D01, 117 XCR, reported on 2026-10-01. "
        "It is ready for review, the deadline is 2026-10-31, and no required field is missing. "
        "The customer message makes no promise. Please read the draft before you approve."
    )
    judge = _Scripted(json.dumps({"verdict": "pass", "reason": reason}))
    approval = record_agent_review(tmp_path, "CARD-D01", draft, 1, critic=judge)
    assert approval["passed"] is True
    assert approval["llm_review"]["verdict"] == "pass"
    assert approval["llm_review"]["reason"] == reason
    assert json.dumps(draft) == before
    assert _saved(tmp_path)["llm_review"]["reason"] == approval["llm_review"]["reason"]
    assert judge.calls == 1


def test_block_stores_the_reason_and_fails_the_gate(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FEATURE_LLM_REVIEW", "block")
    reason = "The customer message promises an outcome. The reviewer still has to read the draft."
    judge = _Scripted(json.dumps({"verdict": "block", "reason": reason}))
    approval = record_agent_review(tmp_path, "CARD-D01", _draft(), 1, critic=judge)
    assert approval["passed"] is False
    assert approval["llm_review"]["verdict"] == "block"
    assert approval["llm_review"]["reason"] == reason


def test_a_two_line_reason_is_kept(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FEATURE_LLM_REVIEW", "block")
    judge = _Scripted('{"verdict": "pass", "reason": "Citations are present.\\nNo outcome is promised."}')
    approval = record_agent_review(tmp_path, "CARD-D01", _draft(), 1, critic=judge)
    assert approval["passed"] is True
    assert approval["llm_review"]["reason"] == "Citations are present.\nNo outcome is promised."


def test_a_three_line_reason_is_kept(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FEATURE_LLM_REVIEW", "block")
    reason = "The purchase is TX-D01.\nThe route is ready for review.\nPlease read the draft."
    judge = _Scripted(json.dumps({"verdict": "pass", "reason": reason}))
    approval = record_agent_review(tmp_path, "CARD-D01", _draft(), 1, critic=judge)
    assert approval["passed"] is True
    assert approval["llm_review"]["reason"] == reason


def test_a_schema_miss_retries_once_then_stores_the_second_reply(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FEATURE_LLM_REVIEW", "block")
    reason = "Citations are present. No outcome is promised."
    judge = _Turns("not json", json.dumps({"verdict": "pass", "reason": reason}))
    approval = record_agent_review(tmp_path, "CARD-D01", _draft(), 1, critic=judge)
    assert judge.calls == 2
    assert approval["passed"] is True
    assert approval["llm_review"]["verdict"] == "pass"
    assert approval["llm_review"]["reason"] == reason


def test_a_missing_or_long_or_bad_reply_blocks_with_the_unusable_note(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FEATURE_LLM_REVIEW", "block")
    replies = [
        '{"verdict": "pass", "reason": ""}',
        '{"verdict": "pass", "reason": "One. Two. Three. Four. Five."}',
        '{"verdict": "pass", "reason": "One.\\nTwo.\\nThree.\\nFour.\\nFive."}',
        "not json",
        '{"verdict": "maybe", "reason": "This is unclear. It still has two sentences."}',
    ]
    for reply in replies:
        approval = record_agent_review(tmp_path, "CARD-D01", _draft(), 1, critic=_Scripted(reply))
        assert approval["passed"] is False
        assert approval["llm_review"]["verdict"] == "block"
        assert approval["llm_review"]["reason"] == "The model reply was not usable."


def test_a_provider_error_skips_the_judge_and_keeps_the_checklist(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FEATURE_LLM_REVIEW", "block")
    approval = record_agent_review(tmp_path, "CARD-D01", _draft(), 1, critic=_Raising())
    assert approval["passed"] is True
    assert approval["checks"]["contract_valid"] is True
    assert "llm_critic" not in approval["checks"]
    assert approval["llm_review"]["mode"] == "skipped"
    assert approval["llm_review"]["reason"] == "The judge step was skipped because the model call failed."


def test_the_judge_is_not_called_when_the_feature_is_off(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("FEATURE_LLM_REVIEW", raising=False)
    judge = _Scripted('{"verdict": "block", "reason": "should not be asked"}')
    approval = record_agent_review(tmp_path, "CARD-D01", _draft(), 1, critic=judge)
    assert approval["passed"] is True
    assert approval["llm_review"] is None
    assert judge.calls == 0


def test_reviewer_note_shows_the_verdict_beside_the_draft():
    draft = _draft()
    reason = (
        "This is an unauthorized complaint for purchase TX-D01, 117 XCR, reported on 2026-10-01. "
        "It is ready for review. Please read the draft."
    )
    note = format_reviewer_note(
        draft,
        {"llm_review": {"verdict": "pass", "reason": reason}},
    )
    assert "Reviewer note for CARD-D01" in note
    assert "Verdict: pass" in note
    assert reason in note
    assert "Purchase: TX-D01" in note
    assert "Amount: 117 XCR" in note
    assert "A pass does not approve the case." in note


def test_reviewer_note_without_a_judge_says_the_checklist_stands():
    note = format_reviewer_note(_draft(), {"llm_review": None})
    assert "No model reason. The checklist result still stands." in note
    assert "Verdict:" not in note


def test_a_failed_checklist_is_not_passed_by_the_judge(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FEATURE_LLM_REVIEW", "block")
    draft = _draft()
    draft["citations"] = []
    judge = _Scripted('{"verdict": "pass", "reason": "Looks fine."}')
    approval = record_agent_review(tmp_path, "CARD-D01", draft, 1, critic=judge)
    assert approval["passed"] is False
    assert judge.calls == 0
