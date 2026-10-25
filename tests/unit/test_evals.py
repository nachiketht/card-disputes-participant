"""Citation support is a substring check. A missed minimum exits non-zero."""

import json
from pathlib import Path

from dispute_copilot.adapters.corpus import MemoryCorpus
from dispute_copilot.evals.citations import LIMIT, JoinedCorpus, citation_support
from dispute_copilot.evals.scorecard import write_scorecard
from dispute_copilot.fields import CASE_ID, CITATION_QUOTE, CITATION_SOURCE_ID, CITATIONS

ROOT = Path(__file__).resolve().parents[2]


def _draft(source: str, quote: str) -> list[dict]:
    return [{CASE_ID: "CARD-D01", CITATIONS: [{CITATION_SOURCE_ID: source, CITATION_QUOTE: quote}]}]


def test_a_quote_must_appear_in_the_cited_source():
    corpus = MemoryCorpus({"CARD-POLICY-V2": "Unauthorized deadline days: 30\n"})
    present = citation_support(corpus, _draft("CARD-POLICY-V2", "Unauthorized deadline days: 30"))
    assert present["supported"] == 1
    assert present["total"] == 1
    assert present["failures"] == []
    assert present["limit"] == LIMIT
    assert "semantic support" in present["limit"]
    missing = citation_support(corpus, _draft("NO-SUCH", "Unauthorized deadline days: 30"))
    assert missing["supported"] == 0
    assert missing["failures"][0]["reason"] == "missing_source"
    absent = citation_support(corpus, _draft("CARD-POLICY-V2", "not in the file"))
    assert absent["failures"][0]["reason"] == "quote_absent"


def test_payload_text_fills_a_source_id_that_is_not_a_file():
    files = MemoryCorpus({"CARD-POLICY-V2": "file line"})
    joined = JoinedCorpus(files, {"CARD-POLICY-V2": "other line", "CARD-D01-TRANSACTIONS": '{"amount": 117}'})
    assert joined.get("CARD-POLICY-V2") == "file line"
    result = citation_support(joined, _draft("CARD-D01-TRANSACTIONS", '"amount": 117'))
    assert result["supported"] == 1
    assert result["total"] == 1


def test_a_missed_minimum_exits_nonzero(tmp_path: Path):
    report = tmp_path / "evals.json"
    code, text = write_scorecard(
        submission=tmp_path / "missing.json",
        data_dir=ROOT / "data" / "dev",
        report_path=report,
        trace_dir=tmp_path / "traces",
        pytest_rows=[],
    )
    assert code == 1
    assert text.endswith("failed")
    assert "does not judge semantic support" in text
    saved = json.loads(report.read_text(encoding="utf-8"))
    assert saved["passed"] is False
    by_id = {item["id"]: item for item in saved["categories"]}
    assert set(by_id) == {
        "workflow_correctness",
        "deadlines",
        "routing",
        "grounding",
        "escalation",
        "injection_resistance",
        "leak_safety",
        "no_commitment",
        "approval_gating",
        "latency",
        "cost",
    }
    assert by_id["workflow_correctness"]["passed"] is False
    assert by_id["latency"]["passed"] is False
    assert by_id["cost"]["passed"] is True
    assert by_id["grounding"]["detail"].startswith("0/0")
