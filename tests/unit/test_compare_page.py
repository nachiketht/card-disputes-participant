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


def test_when_each_approach_wins_is_only_a_proposal():
    from dispute_copilot.classifiers.config import load_settings

    settings = load_settings()
    assert settings.false_negative_ceiling == 0.02
    assert settings.false_positive_low == 0.10
    assert settings.false_positive_high == 0.40
    view = build_comparison({}, {}, min_sample=30)
    view["thresholds"] = {
        "false_negative_ceiling": settings.false_negative_ceiling,
        "false_positive_low": settings.false_positive_low,
        "false_positive_high": settings.false_positive_high,
    }
    page = render_comparison(view)
    assert "When each approach wins" in page
    before, technical = page.split('<details id="technical">', 1)
    assert "Fallback used" not in before
    assert "Fallback used" in technical
    assert "Proposed thresholds, to be confirmed by the A/B test. Not measured results." in page
    assert "False positives under 10%" in page
    assert "false negatives under the ceiling (2%)" in page
    assert "cheapest and fastest" in page
    assert "False positives 10% to 40%" in page
    assert "Jev saves Haiku calls." in page
    assert "False positives over 40%" in page
    assert "Haiku helps nearly every time" in page
    assert "Above the false-negative ceiling, an approach is out, whatever its other numbers." in page
    assert COMPLAINT not in page
    custom = dict(view)
    custom["thresholds"] = {
        "false_negative_ceiling": 0.05,
        "false_positive_low": 0.15,
        "false_positive_high": 0.55,
    }
    shifted = render_comparison(custom)
    assert "under 15%" in shifted
    assert "15% to 55%" in shifted
    assert "over 55%" in shifted
    assert "(5%)" in shifted
    assert COMPLAINT not in shifted


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
    assert second["p95"] == 40
    assert second["cost_per_case"] == 0.003
    assert second["wrong"] == 1
    assert second["sample"] == 2
    assert second["auto_wrong"] == 1
    assert second["auto_sample"] == 2
    assert second["too_small"] is True
    assert second["auto_too_small"] is True
    first = view["matrix"][0][1]
    assert first == {"agree": 1, "shared": 2}
    pair = next(item for item in view["disagreements"] if item["left"] == "1" and item["right"] == "2")
    assert pair["ids"] == ["CARD-B"]
    page = render_comparison(view)
    assert "Wrong-label rate" in page
    assert "Auto-routed but wrong" in page
    assert "Cost per case" in page
    assert "Latency p95" in page
    assert "p50" not in page
    assert "unpriced" in page
    assert "Too small to conclude." in page
    assert "CARD-B" in page
    assert COMPLAINT not in page
    assert '<details id="technical" open' not in page
    assert 'id="technical"' in page
    large = build_comparison(runs, feedback, min_sample=1)
    assert all(item["too_small"] is False and item["auto_too_small"] is False for item in large["approaches"])
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
    assert row["auto_wrong"] == 1
    assert row["auto_sample"] == 1
    assert row["cost_per_case"] == 0.001
    assert row["too_small"] is True


def test_auto_routed_wrong_ignores_a_held_case_and_spreads_cost_over_every_case():
    ready = Routing.READY_FOR_REVIEW.value
    ask = Routing.REQUEST_INFORMATION.value
    manual = Routing.MANUAL_REVIEW.value
    runs = {
        "1": {
            "CARD-A": _row("CARD-A", Classification.UNAUTHORIZED, ready, True, 10, 0.006),
            "CARD-B": _row("CARD-B", Classification.SERVICE_NOT_RECEIVED, ask, False, 20, None),
            "CARD-C": _row("CARD-C", Classification.DUPLICATE_CHARGE, manual, False, 30, None),
            "CARD-D": _row("CARD-D", Classification.UNAUTHORIZED, ready, False, 40, None),
        },
        "2": {},
        "3": {},
    }
    feedback = {
        "CARD-A": Classification.SERVICE_NOT_RECEIVED.value,
        "CARD-B": Classification.DUPLICATE_CHARGE.value,
        "CARD-C": Classification.UNAUTHORIZED.value,
        "CARD-D": Classification.UNAUTHORIZED.value,
    }
    row = next(item for item in build_comparison(runs, feedback, min_sample=30)["approaches"] if item["name"] == "1")
    assert row["wrong"] == 3
    assert row["sample"] == 4
    assert row["auto_wrong"] == 1
    assert row["auto_sample"] == 2
    assert row["manual_count"] == 1
    assert row["cost_per_case"] == 0.0015


def test_export_writes_a_sibling_comparison_page(tmp_path: Path):
    dest = tmp_path / "nested" / "out.html"
    write_html(tmp_path, dest)
    sibling = dest.with_name("compare.html")
    text = sibling.read_text(encoding="utf-8")
    assert "Latency p95" in text
    assert "Cost per case" in text
    assert "p50" not in text
    assert "unpriced" in text
    assert 'href="out.html"' in text
    assert '<details id="technical" open' not in text
    assert "http://" not in text.lower()
    status = dest.read_text(encoding="utf-8")
    assert 'href="compare.html"' in status
