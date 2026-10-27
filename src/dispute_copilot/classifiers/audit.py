"""Saved classification records. The complaint text is not written."""

import json
from pathlib import Path

from dispute_copilot.classifiers.engine import Outcome
from dispute_copilot.evidence.redaction import Redactor
from dispute_copilot.fields import CASE_ID, CLASSIFICATION


def write_audit(
    *,
    case_id: str,
    approach: str,
    outcome: Outcome,
    submissions_dir: Path | None,
    reports_dir: Path | None,
    complaint: str = "",
) -> dict:
    body = record_body(case_id, approach, outcome, complaint)
    text = json.dumps(body, indent=2, sort_keys=True) + "\n"
    if complaint and complaint in text:
        raise RuntimeError("classification record included the complaint")
    redactor = Redactor()
    if redactor.contains_restricted(text):
        body = redactor.scrub(body)
        text = json.dumps(body, indent=2, sort_keys=True) + "\n"
    for folder in _roots(submissions_dir, reports_dir):
        path = folder / CLASSIFICATION / approach / f"{case_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return body


def write_applied(submissions_dir: Path, case_id: str, outcome: Outcome) -> None:
    haiku = outcome.haiku if isinstance(outcome.haiku, dict) else None
    body = {
        CASE_ID: case_id,
        "haiku_called": haiku is not None,
        "haiku_class": None if haiku is None else haiku.get("class"),
        "haiku_reason": "" if haiku is None else haiku.get("reason") or "",
        "haiku_unusable": bool(haiku and haiku.get("unusable")),
        "haiku_quote_missing": bool(haiku and haiku.get("evidence_ok") is False and not haiku.get("unusable")),
    }
    path = submissions_dir / CLASSIFICATION / "applied" / f"{case_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_assignment(submissions_dir: Path, case_id: str, approach: str) -> None:
    path = submissions_dir / CLASSIFICATION / "assignments" / f"{case_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"approach": approach, CASE_ID: case_id}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def record_body(case_id: str, approach: str, outcome: Outcome, complaint: str = "") -> dict:
    return {
        "approach": approach,
        "called_model": outcome.called_model,
        CASE_ID: case_id,
        "final_class": outcome.decision.classification.value,
        "haiku": _without_complaint(outcome.haiku, complaint),
        "jev": _without_complaint(outcome.jev, complaint),
        "latency_ms": outcome.latency_ms,
        "missing": [item.value for item in outcome.decision.missing],
        "path": outcome.path,
        "route": outcome.route,
        "rules": {
            "class": outcome.rules.classification.value,
            "conflict": outcome.rules.conflict,
            "missing": [item.value for item in outcome.rules.missing],
        },
    }


def _without_complaint(value, complaint: str):
    if not complaint or not isinstance(value, dict):
        return value
    cleaned = {}
    for key, item in value.items():
        if isinstance(item, str) and complaint in item:
            cleaned[key] = ""
        else:
            cleaned[key] = item
    if "evidence" in cleaned and cleaned["evidence"] == "":
        cleaned["evidence_ok"] = False
    return cleaned


def _roots(*folders: Path | None) -> list[Path]:
    found = []
    for folder in folders:
        if folder is not None and folder not in found:
            found.append(folder)
    return found
