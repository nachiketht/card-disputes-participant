"""Citation support is a substring check. A missed minimum exits non-zero."""

import json
from pathlib import Path

from dispute_copilot.adapters.corpus import MemoryCorpus
from dispute_copilot.evals.citations import LIMIT, JoinedCorpus, citation_support
from dispute_copilot.evals.scorecard import (
    _check_detail,
    _check_rate,
    _commitment,
    _cost_category,
    _drafts,
    _escalation,
    _extra_rate,
    _min_rate,
    _official,
    _payload_sources,
    _ratio,
    _row,
    _set_rows,
    write_scorecard,
)
from dispute_copilot.fields import CASE_ID, CITATION_QUOTE, CITATION_SOURCE_ID, CITATIONS, NARRATIVE, ROUTING

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


def test_scorecard_helpers_fail_closed_on_bad_inputs(tmp_path: Path, monkeypatch):
    assert _drafts(tmp_path / "missing.json") == []
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    assert _drafts(bad) == []
    bad.write_text(json.dumps({"case_id": "X"}), encoding="utf-8")
    assert _drafts(bad) == []
    listed = tmp_path / "ok.json"
    listed.write_text(json.dumps([{CASE_ID: "A"}, "skip", {CASE_ID: "B"}]), encoding="utf-8")
    assert [row[CASE_ID] for row in _drafts(listed)] == ["A", "B"]
    missing = _official(ROOT / "data" / "dev", ROOT / "feedback" / "dev_gold.json", tmp_path / "nope.json")
    assert missing["format_errors"] == ["missing submission"]
    monkeypatch.setattr(
        "dispute_copilot.evals.scorecard.subprocess.run",
        lambda *a, **k: type("P", (), {"returncode": 0, "stdout": "not-json"})(),
    )
    broken = _official(ROOT / "data" / "dev", ROOT / "feedback" / "dev_gold.json", listed)
    assert broken["format_errors"] == ["evaluator output was not json"]
    monkeypatch.setattr(
        "dispute_copilot.evals.scorecard.subprocess.run",
        lambda *a, **k: type("P", (), {"returncode": 1, "stdout": ""})(),
    )
    assert _payload_sources(ROOT / "data" / "dev") == {}
    monkeypatch.setattr(
        "dispute_copilot.evals.scorecard.subprocess.run",
        lambda *a, **k: type("P", (), {"returncode": 0, "stdout": json.dumps({"sources": ["x"]})})(),
    )
    assert _payload_sources(ROOT / "data" / "dev") == {}


def test_score_math_and_category_rows(tmp_path: Path):
    assert _ratio(1, 2) == 0.5
    assert _ratio(1, 0) is None
    assert _ratio("1", 2) is None
    assert _min_rate(None, None) is None
    assert _min_rate(0.9, 0.2) == 0.2
    assert _check_rate({"cases": []}, "deadline") is None
    assert _check_rate({"cases": [{"checks": {"deadline": True}}, "x"]}, "deadline") == 0.5
    assert _check_detail({"cases": [{"checks": {"deadline": True}}]}, "deadline") == "1/1"
    gold = {"CARD-D01": {ROUTING: ["ready_for_review", "manual_review"]}, "CARD-D02": {ROUTING: "ready_for_review"}}
    by_id = {"CARD-D01": {ROUTING: "manual_review", NARRATIVE: "We cannot confirm an outcome in this draft."}}
    score, detail = _escalation(gold, by_id)
    assert score == 1.0
    assert detail == "1/1"
    assert _escalation({}, {})[1] == "no escalation cases"
    assert _commitment({}, {})[1] == "no cases"
    score, detail = _commitment({"CARD-D01": {}}, by_id)
    assert score == 1.0
    none = _extra_rate({}, {}, "routing")
    assert none is None
    assert _extra_rate({"n": {"category": "routing"}}, {"n": True}, "routing") == 1.0
    row = _row("cost", "cost", None, None, "none")
    assert row["passed"] is True
    rows = _set_rows(
        [{"kind": "official", "id": "dev", "version": 1, "nodes": []}, {"id": "syn", "version": 1, "nodes": [{"node": "t::x"}, "z"]}],
        {"passed_cases": 12, "total_cases": 12},
        {"t::x": True},
    )
    assert rows[0]["passed_cases"] == 12
    assert rows[1] == {"id": "syn", "version": 1, "passed_cases": 1, "total_cases": 1}
    traces = tmp_path / "traces"
    traces.mkdir()
    (traces / "C.jsonl").write_text(
        json.dumps(
            {
                "kind": "span",
                "name": "model.call",
                "attrs": {"model": "claude-haiku-5-5", "tokens_in": 1000, "tokens_out": 500},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    priced = _cost_category(traces, {"minimum": {}, "cost": {"maximum_usd": 1}}, "cost")
    assert priced["passed"] is True
    assert priced["score"] is not None
    unknown_calls = tmp_path / "other"
    unknown_calls.mkdir()
    (unknown_calls / "C.jsonl").write_text(
        json.dumps({"kind": "span", "name": "model.call", "attrs": {"model": "unknown-model", "tokens_in": 1, "tokens_out": 1}})
        + "\n",
        encoding="utf-8",
    )
    unpriced = _cost_category(unknown_calls, {"minimum": {}, "cost": {"maximum_usd": 1}}, "cost")
    assert unpriced["passed"] is False
    empty = _cost_category(tmp_path / "missing", {"minimum": {}, "cost": {"maximum_usd": 1}}, "cost")
    assert empty["passed"] is True
