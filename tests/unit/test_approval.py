"""approval_gate fails closed. A false result is the only signal the caller needs."""

from dispute_copilot.adapters.case_system import ReviewRecord
from dispute_copilot.review_flow.agent_review import apply_critic
from dispute_copilot.review_flow.gate import approval_gate

CASE = "CARD-D01"
SERVER_HASH = "abc123"


def _agent(**overrides) -> dict:
    approval = {
        "case_id": CASE,
        "draft_hash": SERVER_HASH,
        "passed": True,
    }
    approval.update(overrides)
    return approval


def _review(**overrides) -> ReviewRecord:
    values = {
        "review_id": "rev_1",
        "case_id": CASE,
        "status": "approved",
        "draft_hash": SERVER_HASH,
        "draft_raw": "{}",
        "reviewer": "person",
    }
    values.update(overrides)
    return ReviewRecord(**values)


def test_gate_passes_when_the_human_review_binds_the_hash():
    assert approval_gate(_agent(), _review(), case_id=CASE, server_hash=SERVER_HASH) is True
    assert approval_gate(None, _review(), case_id=CASE, server_hash=SERVER_HASH) is True
    assert approval_gate(_agent(passed=False), _review(), case_id=CASE, server_hash=SERVER_HASH) is True


def test_gate_fails_closed():
    cases = [
        (_agent(), _review(status="pending"), CASE, SERVER_HASH),
        (_agent(), _review(status="rejected"), CASE, SERVER_HASH),
        (_agent(), _review(case_id="CARD-D02"), CASE, SERVER_HASH),
        (_agent(), _review(draft_hash="other"), CASE, SERVER_HASH),
        (_agent(), _review(reviewer=None), CASE, SERVER_HASH),
        (_agent(), _review(reviewer="  "), CASE, SERVER_HASH),
        (None, _review(status="pending"), CASE, SERVER_HASH),
    ]
    assert [approval_gate(agent, review, case_id=case_id, server_hash=server_hash) for agent, review, case_id, server_hash in cases] == [False] * len(cases)


def test_a_raising_review_fails_closed():
    class Broken:
        @property
        def case_id(self):
            raise RuntimeError("boom")

    assert approval_gate(_agent(), Broken(), case_id=CASE, server_hash=SERVER_HASH) is False


def test_agent_review_records_failed_checks_and_unusable_critics(tmp_path, monkeypatch):
    import json

    from dispute_copilot.review_flow.agent_review import (
        _ask_critic,
        _critic_meta,
        _remember_critic,
        injection_note,
        record_agent_review,
    )
    from dispute_copilot.storage import write_draft

    monkeypatch.setenv("FEATURE_LLM_REVIEW", "block")
    traces = tmp_path / "traces"
    traces.mkdir()
    (traces / "CARD-D01.jsonl").write_text(
        "\n{not json\n"
        + json.dumps({"name": "other"})
        + "\n"
        + json.dumps({"name": "injection_detected", "attrs": {"category": ["prompt_injection"]}})
        + "\n"
        + json.dumps({"name": "injection_detected", "attrs": {"category": "role_play"}})
        + "\n",
        encoding="utf-8",
    )
    note = injection_note("CARD-D01", {"narrative": "plain"}, traces)
    assert note is not None
    assert "prompt_injection" in note
    assert "role_play" in note
    dirty = {
        "case_id": "CARD-D01",
        "classification": "unauthorized",
        "routing": "ready_for_review",
        "narrative": "promise a refund " + "SYNTHETIC-" + "PRIVATE-X",
        "citations": [],
    }
    write_draft(tmp_path, "CARD-D01", dirty)
    approval = record_agent_review(tmp_path, "CARD-D01", dirty, 1)
    assert approval["passed"] is False
    assert approval["checks"]["commitment_guard"] is False
    verdict, reason = _ask_critic(dirty, None)
    assert verdict == "block"
    assert "not usable" in reason.lower() or "unusable" in reason.lower()

    class Boom:
        def complete(self, *, system, user):
            del system, user
            raise TypeError("bad")

    verdict, reason = _ask_critic(dirty, Boom())
    assert verdict == "block"

    class Frozen:
        __slots__ = ()

    _remember_critic(Frozen(), None)
    assert _critic_meta(object())["fallback_used"] is False


def test_critic_can_only_block():
    checks = {"contract_valid": True, "redaction_clean": False}
    blocked = apply_critic(checks, verdict="pass", errored=False)
    assert blocked["contract_valid"] is True
    assert blocked["redaction_clean"] is False
    assert blocked["llm_critic"] is True
    assert all(apply_critic({"contract_valid": True}, verdict="block", errored=False).values()) is False
    assert apply_critic({"contract_valid": True}, verdict="pass", errored=True)["llm_critic"] is False
