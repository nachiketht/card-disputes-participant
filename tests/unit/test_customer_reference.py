"""A customer reference inside a complaint is masked, not a failed run."""

import json
from pathlib import Path

from dispute_copilot.adapters.case_system import FakeCaseSystem
from dispute_copilot.adapters.corpus import FileCorpus, MemoryCorpus
from dispute_copilot.adapters.tools import FakeToolClient
from dispute_copilot.evidence.redaction import Redactor
from dispute_copilot.observability.logs import configure
from dispute_copilot.observability.tracing import LOGGER, case_trace
from dispute_copilot.workflow import prepare

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "dev"
REF = "CUST" + "-D01"
ORIGINAL = (
    "I found a card purchase at Morrow Supplies for 117 crowns. "
    "I do not recognize it and need help checking it."
)


def _corpus(complaint: str) -> MemoryCorpus:
    loaded = FileCorpus(DATA)
    texts = {source_id: loaded.get(source_id) for source_id in loaded.ids()}
    intake_id = "CARD-D01-INTAKE"
    original = texts[intake_id]
    start = original.index(ORIGINAL)
    end = original.index("\n\n", start)
    texts[intake_id] = original[:start] + complaint + original[end:]
    return MemoryCorpus(texts)


def _draft(tmp_path: Path, complaint: str) -> dict:
    case_id = "CARD-D01"
    payload = {
        "source_id": f"{case_id}-TRANSACTIONS",
        "transactions": [
            {
                "transaction_id": "TX-D01",
                "amount": 117,
                "currency": "XCR",
                "posted_date": "2026-08-19",
            }
        ],
    }
    merchant = {"source_id": "MERCHANT", "display_name": "Morrow Supplies", "note": "ordinary note"}
    tools = FakeToolClient(
        cases=[],
        transactions={case_id: [(200, payload)]},
        merchants={case_id: [(200, merchant)]},
    )
    case = {"case_id": case_id, "intake_source": f"{case_id}-INTAKE", "documents": [f"{case_id}-INTAKE"]}
    with case_trace(case_id, "run-ref", tmp_path):
        return prepare(
            case_id,
            corpus=_corpus(complaint),
            tools=tools,
            case_system=FakeCaseSystem(),
            case=case,
        )


def _restore_logs(previous) -> None:
    LOGGER.handlers.clear()
    for handler in previous:
        LOGGER.addHandler(handler)


def test_a_customer_reference_in_the_complaint_is_masked(tmp_path):
    previous = list(LOGGER.handlers)
    log_path = tmp_path / "run.log"
    configure(log_path)
    try:
        complaint = ORIGINAL.replace("I do not recognize", f"My ref is {REF}. I do not recognize")
        draft = _draft(tmp_path, complaint)
    finally:
        _restore_logs(previous)
    rendered = json.dumps(draft)
    assert REF not in rendered
    assert draft["classification"] == "unauthorized"
    assert draft["routing"] == "ready_for_review"
    assert draft["facts"]["amount"] == 117
    assert draft["facts"]["currency"] == "XCR"
    quotes = [item["quote"] for item in draft["citations"] if item["claim"] == "customer_evidence"]
    assert any("My ref is [redacted]" in quote for quote in quotes)
    trace = (tmp_path / "CARD-D01.jsonl").read_text(encoding="utf-8")
    assert REF not in trace
    assert "redaction.applied" in trace
    logged = log_path.read_text(encoding="utf-8")
    assert REF not in logged
    assert "redaction.applied" in logged


def test_an_unmaskable_quote_is_escalated(tmp_path):
    previous = list(LOGGER.handlers)
    log_path = tmp_path / "run.log"
    configure(log_path)
    try:
        draft = _draft(tmp_path, REF)
    finally:
        _restore_logs(previous)
    rendered = json.dumps(draft)
    assert REF not in rendered
    assert "[redacted]" not in rendered
    assert draft["classification"] == "unauthorized"
    assert draft["routing"] == "manual_review"
    assert draft["facts"]["amount"] == 117
    assert "could not be masked safely" in draft["narrative"]
    assert "manual review" in draft["narrative"]
    quotes = [item["quote"] for item in draft["citations"]]
    assert all(REF not in quote for quote in quotes)
    trace = (tmp_path / "CARD-D01.jsonl").read_text(encoding="utf-8")
    assert REF not in trace
    assert "redaction.withheld" in trace
    assert REF not in log_path.read_text(encoding="utf-8")


def test_mask_keeps_surrounding_words_and_drops_a_bare_token():
    redactor = Redactor()
    masked = redactor.mask(f"My ref is {REF} today")
    assert masked == "My ref is [redacted] today"
    assert redactor.mask_quote(f"My ref is {REF}") == "My ref is [redacted]"
    assert redactor.mask_quote(REF) is None
    assert redactor.mask("Morrow Supplies") == "Morrow Supplies"
    token = "SYNTHETIC-" + "PRIVATE-TEST"
    assert token not in redactor.mask("prefix " + token + " suffix")
    assert "private_" + "token" not in redactor.mask("see private_" + "token now")
