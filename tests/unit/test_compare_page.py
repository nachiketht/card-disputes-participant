"""Comparison page: rates, matrix, case ids, and no complaint text."""

import json
from pathlib import Path

from dispute_copilot.classifiers.audit import write_audit
from dispute_copilot.classifiers.engine import Outcome
from dispute_copilot.fields import CASE_ID, CLASSIFICATION, Classification, Routing
from dispute_copilot.observability.board import bind_page, write_html
from dispute_copilot.observability.compare_page import build_comparison, load_comparison, render_comparison
from dispute_copilot.rules.classify import Decision

COMPLAINT = "I found a card purchase that should never be copied onto the page."


def _row(case_id: str, label: Classification, route: str, called: bool, latency: float, usd: float | None) -> dict:
    return {
        CASE_ID: case_id,
        "final_class": label.value,
        "route": route,
        "called_model": called,
        "latency_ms": latency,
        "haiku_usd": usd,
    }


def test_comparison_rates_matrix_and_the_small_sample_note():
    manual = Routing.MANUAL_REVIEW.value
    ready = Routing.READY_FOR_REVIEW.value
    runs = {
        "1": {
            "CARD-A": _row("CARD-A", Classification.UNAUTHORIZED, ready, False, 10, None),
            "CARD-B": _row("CARD-B", Classification.DUPLICATE_CHARGE, manual, False, 20, None),
            "CARD-C": _row("CARD-C", Classification.SERVICE_NOT_RECEIVED, ready, False, 30, None),
            "CARD-D": _row("CARD-D", Classification.AMBIGUOUS, manual, False, 40, None),
        },
        "2": {
            "CARD-A": _row("CARD-A", Classification.UNAUTHORIZED, ready, True, 10, 0.002),
            "CARD-B": _row("CARD-B", Classification.SERVICE_NOT_RECEIVED, ready, True, 40, 0.004),
        },
        "3": {
            "CARD-A": _row("CARD-A", Classification.UNAUTHORIZED, ready, True, 15, None),
        },
    }
    feedback = {"CARD-A": Classification.UNAUTHORIZED.value, "CARD-B": Classification.DUPLICATE_CHARGE.value}
    view = build_comparison(runs, feedback, min_sample=30)
    second = next(item for item in view["approaches"] if item["name"] == "2")
    assert second["cases"] == 2
    assert second["called_model"] == 2
    assert second["manual_count"] == 0
    assert second["p50"] == 10
    assert second["p95"] == 40
    assert second["haiku_per_case"] == 0.003
    assert second["wrong"] == 1
    assert second["sample"] == 2
    assert second["too_small"] is True
    first = view["matrix"][0][1]
    assert first == {"agree": 1, "shared": 2}
    pair = next(item for item in view["disagreements"] if item["left"] == "1" and item["right"] == "2")
    assert pair["ids"] == ["CARD-B"]
    page = render_comparison(view)
    assert "p50" in page and "p95" in page
    assert "unpriced" in page
    assert "Too small to conclude." in page
    assert "CARD-B" in page
    assert COMPLAINT not in page
    assert '<details id="technical" open' not in page
    assert 'id="technical"' in page
    large = build_comparison(runs, feedback, min_sample=1)
    assert all(item["too_small"] is False for item in large["approaches"])
    assert "Too small to conclude." not in render_comparison(large)


def test_saved_records_drop_complaint_text_and_keep_jev_unpriced(tmp_path: Path):
    rules = Decision(Classification.UNAUTHORIZED, (), False)
    haiku = {
        "class": Classification.UNAUTHORIZED.value,
        "reason": COMPLAINT,
        "evidence": COMPLAINT,
        "cost": {"status": "estimate from list price", "usd": 0.001},
    }
    jev = {
        "class": Classification.UNAUTHORIZED.value,
        "probabilities": {},
        "cost": {"status": "unpriced", "usd": 9.99},
    }
    outcome = Outcome(rules, "haiku_assigned", rules, jev, haiku, True, 12.5, Routing.READY_FOR_REVIEW.value)
    write_audit(
        case_id="CARD-A",
        approach="2",
        outcome=outcome,
        submissions_dir=tmp_path,
        reports_dir=None,
        complaint=COMPLAINT,
    )
    raw_path = tmp_path / CLASSIFICATION / "2" / "CARD-A.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    raw["complaint"] = COMPLAINT
    raw_path.write_text(json.dumps(raw), encoding="utf-8")
    (tmp_path / "feedback").mkdir()
    (tmp_path / "feedback" / "CARD-A.json").write_text(
        json.dumps({"category": Classification.SERVICE_NOT_RECEIVED.value, "note": COMPLAINT}),
        encoding="utf-8",
    )
    view = load_comparison(tmp_path)
    page = render_comparison(view)
    assert COMPLAINT not in page
    assert "9.99" not in page
    assert "unpriced" in page
    row = next(item for item in view["approaches"] if item["name"] == "2")
    assert row["cases"] == 1
    assert row["wrong"] == 1
    assert row["sample"] == 1
    assert row["too_small"] is True


def test_export_writes_a_sibling_comparison_page(tmp_path: Path):
    dest = tmp_path / "nested" / "out.html"
    write_html(tmp_path, dest)
    sibling = dest.with_name("compare.html")
    text = sibling.read_text(encoding="utf-8")
    assert "p50" in text and "p95" in text
    assert "unpriced" in text
    assert 'href="out.html"' in text
    assert '<details id="technical" open' not in text
    assert "http://" not in text.lower()
    status = dest.read_text(encoding="utf-8")
    assert 'href="compare.html"' in status
