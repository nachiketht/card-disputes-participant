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


def test_gate_passes_only_when_both_steps_bind_the_same_hash():
    assert approval_gate(_agent(), _review(), case_id=CASE, server_hash=SERVER_HASH) is True


def test_gate_fails_closed():
    cases = [
        (None, _review(), CASE, SERVER_HASH),
        (_agent(passed=False), _review(), CASE, SERVER_HASH),
        (_agent(case_id="CARD-D02"), _review(), CASE, SERVER_HASH),
        (_agent(draft_hash="other"), _review(), CASE, SERVER_HASH),
        (_agent(), _review(status="pending"), CASE, SERVER_HASH),
        (_agent(), _review(status="rejected"), CASE, SERVER_HASH),
        (_agent(), _review(case_id="CARD-D02"), CASE, SERVER_HASH),
        (_agent(), _review(draft_hash="other"), CASE, SERVER_HASH),
        (_agent(), _review(reviewer=None), CASE, SERVER_HASH),
        (_agent(), _review(reviewer="  "), CASE, SERVER_HASH),
    ]
    assert [approval_gate(agent, review, case_id=case_id, server_hash=server_hash) for agent, review, case_id, server_hash in cases] == [False] * len(cases)


def test_a_raising_review_fails_closed():
    class Broken:
        @property
        def case_id(self):
            raise RuntimeError("boom")

    assert approval_gate(_agent(), Broken(), case_id=CASE, server_hash=SERVER_HASH) is False


def test_critic_can_only_block():
    checks = {"contract_valid": True, "redaction_clean": False}
    blocked = apply_critic(checks, verdict="pass", errored=False)
    assert blocked["contract_valid"] is True
    assert blocked["redaction_clean"] is False
    assert blocked["llm_critic"] is True
    assert all(apply_critic({"contract_valid": True}, verdict="block", errored=False).values()) is False
    assert apply_critic({"contract_valid": True}, verdict="pass", errored=True)["llm_critic"] is False
