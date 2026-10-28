"""Comparison page for saved classification runs.

The page lists counts, rates, and case ids. It does not copy complaint text,
model reasons, or evidence quotes.
"""

import json
from pathlib import Path

from dispute_copilot.classifiers.config import load_settings
from dispute_copilot.evidence.redaction import Redactor
from dispute_copilot.fields import CASE_ID, CLASSIFICATION, Routing
from dispute_copilot.observability.board import page_css
from dispute_copilot.observability.cost import LIST_PRICE
from dispute_copilot.observability.metrics import percentile
from dispute_copilot.observability.path import PAGE_SCRIPT

_APPROACHES = ("1", "2", "3")


def load_comparison(reports_dir: Path, submissions_dir: Path | None = None) -> dict:
    roots = _roots(reports_dir, submissions_dir)
    settings = load_settings()
    view = build_comparison(
        _read_runs(roots),
        _read_feedback(roots),
        min_sample=settings.min_sample,
    )
    view["thresholds"] = {
        "false_negative_ceiling": settings.false_negative_ceiling,
        "false_positive_low": settings.false_positive_low,
        "false_positive_high": settings.false_positive_high,
    }
    return view


def build_comparison(runs: dict[str, dict[str, dict]], feedback: dict[str, str], *, min_sample: int) -> dict:
    """Summarize one row per approach. `runs` maps approach to case id to a safe row."""
    approaches = []
    for name in _APPROACHES:
        rows = runs.get(name) or {}
        approaches.append(_approach(name, rows, feedback, min_sample))
    return {
        "min_sample": min_sample,
        "approaches": approaches,
        "matrix": _matrix(runs),
        "disagreements": _disagreements(runs),
        "cases": _case_rows(runs),
    }


def render_comparison(view: dict, back_href: str = "index.html") -> str:
    page = _markup(view, back_href)
    redactor = Redactor()
    if not redactor.contains_restricted(page):
        return page
    masked = redactor.mask(page)
    if redactor.contains_restricted(masked):
        return "<!DOCTYPE html><title>redacted</title><p>redacted</p>"
    return masked


def _approach(name: str, rows: dict[str, dict], feedback: dict[str, str], min_sample: int) -> dict:
    ordered = [rows[key] for key in sorted(rows)]
    called = sum(1 for row in ordered if row.get("called_model") is True)
    manual = sum(1 for row in ordered if row.get("route") == Routing.MANUAL_REVIEW.value)
    latencies = [float(row["latency_ms"]) for row in ordered if isinstance(row.get("latency_ms"), (int, float))]
    priced = [float(row["haiku_usd"]) for row in ordered if isinstance(row.get("haiku_usd"), (int, float))]
    paired = [row for row in ordered if row.get(CASE_ID) in feedback]
    wrong = sum(1 for row in paired if _label_wrong(row, feedback))
    ready = Routing.READY_FOR_REVIEW.value
    auto = [row for row in paired if row.get("route") == ready]
    auto_wrong = sum(1 for row in auto if _label_wrong(row, feedback))
    sample = len(paired)
    auto_sample = len(auto)
    cases = len(ordered)
    return {
        "name": name,
        "cases": cases,
        "called_model": called,
        "manual_count": manual,
        "p95": None if not latencies else round(percentile(latencies, 0.95) or 0, 3),
        "priced_calls": len(priced),
        "cost_per_case": None if cases == 0 else round(sum(priced) / cases, 6),
        "wrong": wrong,
        "sample": sample,
        "auto_wrong": auto_wrong,
        "auto_sample": auto_sample,
        "too_small": sample < min_sample,
        "auto_too_small": auto_sample < min_sample,
    }


def _label_wrong(row: dict, feedback: dict[str, str]) -> bool:
    return row.get("final_class") != feedback.get(str(row.get(CASE_ID)))


def _matrix(runs: dict[str, dict[str, dict]]) -> list[list[dict]]:
    rows = []
    for left in _APPROACHES:
        cells = []
        for right in _APPROACHES:
            shared = sorted(set(runs.get(left) or {}) & set(runs.get(right) or {}))
            agree = 0
            for case_id in shared:
                if (runs[left][case_id].get("final_class")) == (runs[right][case_id].get("final_class")):
                    agree += 1
            cells.append({"agree": agree, "shared": len(shared)})
        rows.append(cells)
    return rows


def _disagreements(runs: dict[str, dict[str, dict]]) -> list[dict]:
    found = []
    names = list(_APPROACHES)
    for index, left in enumerate(names):
        for right in names[index + 1 :]:
            shared = sorted(set(runs.get(left) or {}) & set(runs.get(right) or {}))
            ids = [
                case_id
                for case_id in shared
                if runs[left][case_id].get("final_class") != runs[right][case_id].get("final_class")
            ]
            found.append({"left": left, "right": right, "ids": ids, "shared": len(shared)})
    return found


def _case_rows(runs: dict[str, dict[str, dict]]) -> list[dict]:
    rows = []
    for name in _APPROACHES:
        for case_id in sorted(runs.get(name) or {}):
            row = runs[name][case_id]
            rows.append(
                {
                    "approach": name,
                    CASE_ID: case_id,
                    "final_class": str(row.get("final_class") or ""),
                    "route": str(row.get("route") or ""),
                    "latency_ms": row.get("latency_ms"),
                    "haiku_usd": row.get("haiku_usd"),
                    "jev_usd": row.get("jev_usd"),
                    "fallback_used": row.get("fallback_used") is True,
                }
            )
    return rows


def _read_runs(roots: list[Path]) -> dict[str, dict[str, dict]]:
    found = {name: {} for name in _APPROACHES}
    for root in roots:
        base = root / CLASSIFICATION
        if not base.is_dir():
            continue
        for name in _APPROACHES:
            folder = base / name
            if not folder.is_dir():
                continue
            for path in sorted(folder.glob("*.json")):
                case_id = path.stem
                if not _safe(case_id) or case_id in found[name]:
                    continue
                row = _safe_row(path, case_id)
                if row is not None:
                    found[name][case_id] = row
    return found


def _read_feedback(roots: list[Path]) -> dict[str, str]:
    found: dict[str, str] = {}
    for root in roots:
        folder = root / "feedback"
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.json")):
            case_id = path.stem
            if not _safe(case_id) or case_id in found:
                continue
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeError):
                continue
            if not isinstance(loaded, dict):
                continue
            category = loaded.get("category")
            if isinstance(category, str) and category.strip():
                found[case_id] = category.strip()
    return found


def _safe_row(path: Path, case_id: str) -> dict | None:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(loaded, dict):
        return None
    stored = loaded.get(CASE_ID)
    if isinstance(stored, str) and stored != case_id:
        return None
    haiku = loaded.get("haiku") if isinstance(loaded.get("haiku"), dict) else {}
    jev = loaded.get("jev") if isinstance(loaded.get("jev"), dict) else {}
    return {
        CASE_ID: case_id,
        "final_class": loaded.get("final_class") if isinstance(loaded.get("final_class"), str) else "",
        "route": loaded.get("route") if isinstance(loaded.get("route"), str) else "",
        "called_model": loaded.get("called_model") is True,
        "latency_ms": loaded.get("latency_ms") if isinstance(loaded.get("latency_ms"), (int, float)) else None,
        "haiku_usd": _usd(haiku.get("cost") if isinstance(haiku, dict) else None),
        "jev_usd": _listed_usd(jev.get("cost") if isinstance(jev, dict) else None),
        "fallback_used": haiku.get("fallback_used") is True if isinstance(haiku, dict) else False,
    }


def _usd(cost: object) -> float | None:
    if not isinstance(cost, dict):
        return None
    value = cost.get("usd")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _listed_usd(cost: object) -> float | None:
    """A Jev figure counts only when the saved status is the list-price label."""
    if not isinstance(cost, dict) or cost.get("status") != LIST_PRICE:
        return None
    return _usd(cost)


def _markup(view: dict, back_href: str) -> str:
    from html import escape

    parts = [
        "<!DOCTYPE html>",
        "<html lang=\"en\"><head><meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        "<title>Comparison</title>",
        f"<style>{page_css()}</style></head><body>",
        "<header><div class=\"top\">",
        f"<a class=\"mark\" href=\"{escape(back_href)}\">Run status</a>",
        "<p class=\"mark\">Read-only</p>",
        "<button type=\"button\" id=\"theme-toggle\">Dark</button></div>",
        "<h1>Comparison</h1>",
        "<p class=\"lede\">Saved classification runs. The five rates are wrong-label, auto-routed but wrong, manual review, configured-model cost per case, and latency p95. This page has case ids and counts. It does not include complaint text.</p>",
        "</header><main>",
        _approach_table(view),
        _wins_table(view),
        _matrix_section(view),
        _disagree_section(view),
        "<details id=\"technical\"><summary>Technical details</summary><div class=\"tech\">",
        _case_table(view),
        "</div></details>",
        "</main>",
        PAGE_SCRIPT,
        "</body></html>",
    ]
    return "".join(parts)


def _approach_table(view: dict) -> str:
    from html import escape

    head = (
        "<tr><th>Approach</th><th>Cases run</th><th>Wrong-label rate</th>"
        "<th>Auto-routed but wrong</th><th>Manual review</th>"
        "<th>Cost per case</th><th>Latency p95</th></tr>"
    )
    body = []
    notes = []
    floor = view["min_sample"]
    for row in view["approaches"]:
        total = row["cases"]
        body.append(
            "<tr>"
            f"<td>{escape(row['name'])}</td>"
            f"<td>{total}</td>"
            f"<td>{escape(_share(row['wrong'], row['sample']))}</td>"
            f"<td>{escape(_share(row['auto_wrong'], row['auto_sample']))}</td>"
            f"<td>{escape(_share(row['manual_count'], total))}</td>"
            f"<td>{escape(_cost(row))}</td>"
            f"<td>{escape(_ms(row['p95']))}</td>"
            "</tr>"
        )
        if row["too_small"]:
            notes.append(
                f"Approach {row['name']} wrong-label sample size {row['sample']} is below {floor}. Too small to conclude."
            )
        if row["auto_too_small"]:
            notes.append(
                f"Approach {row['name']} auto-routed sample size {row['auto_sample']} is below {floor}. Too small to conclude."
            )
    defined = (
        "<p class=\"note\">Wrong-label rate uses every case with a reviewer category. "
        "Auto-routed but wrong uses only those cases whose route is ready_for_review. "
        "Manual review is that route over cases run. "
        "Cost per case is the configured-model list-price USD over cases run. "
        "Jev is priced from the vendor list in config/rules/cost.yaml at 0.042 USD per million input tokens, and output tokens are free. "
        "Latency p95 is the classification audit.</p>"
    )
    note = "".join(f"<p class=\"note\">{escape(line)}</p>" for line in notes)
    return (
        "<section id=\"approaches\"><h2>Approaches</h2>"
        f"{defined}"
        f"<table><thead>{head}</thead><tbody>{''.join(body)}</tbody></table>"
        f"{note}</section>"
    )


def _wins_table(view: dict) -> str:
    """Static proposal. The numbers are cut-offs, not a measurement of these runs."""
    from html import escape

    raw = view.get("thresholds") if isinstance(view.get("thresholds"), dict) else {}
    ceiling = _cut(raw.get("false_negative_ceiling"), 0.02)
    low = _cut(raw.get("false_positive_low"), 0.10)
    high = _cut(raw.get("false_positive_high"), 0.40)
    rows = (
        (
            f"False positives under {_pct(low)} and false negatives under the ceiling ({_pct(ceiling)})",
            "Approach 1: cheapest and fastest; a model adds little.",
        ),
        (
            f"False positives {_pct(low)} to {_pct(high)} and false negatives under the ceiling",
            "Approach 3: most flagged cases are truly ambiguous; Jev saves Haiku calls.",
        ),
        (
            f"False positives over {_pct(high)} and false negatives under the ceiling",
            "Approach 2: most flagged cases are really clear; Haiku helps nearly every time; Jev only adds cost and delay.",
        ),
    )
    body = "".join(
        f"<tr><td>{escape(condition)}</td><td>{escape(choice)}</td></tr>" for condition, choice in rows
    )
    return (
        "<section id=\"when-wins\"><h2>When each approach wins</h2>"
        "<p class=\"note\" id=\"threshold-label\">Proposed thresholds, to be confirmed by the A/B test. Not measured results.</p>"
        "<table><thead><tr><th>False positives and false negatives</th><th>What to use</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
        "<p class=\"note\">Above the false-negative ceiling, an approach is out, whatever its other numbers.</p>"
        "</section>"
    )


def _cut(value: object, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def _pct(value: float) -> str:
    text = f"{value * 100:.2f}".rstrip("0").rstrip(".")
    return f"{text}%"


def _matrix_section(view: dict) -> str:
    from html import escape

    head = "<tr><th>Agreement</th>" + "".join(f"<th>{escape(name)}</th>" for name in _APPROACHES) + "</tr>"
    body = []
    for name, cells in zip(_APPROACHES, view["matrix"], strict=True):
        rendered = "".join(f"<td>{escape(_pair(cell['agree'], cell['shared']))}</td>" for cell in cells)
        body.append(f"<tr><th>{escape(name)}</th>{rendered}</tr>")
    return (
        "<section id=\"agreement\"><h2>Agreement</h2>"
        "<p class=\"note\">Each cell is the cases that share a class, over the cases saved for that pair.</p>"
        f"<table><thead>{head}</thead><tbody>{''.join(body)}</tbody></table></section>"
    )


def _disagree_section(view: dict) -> str:
    from html import escape

    lines = []
    for item in view["disagreements"]:
        if item["shared"] == 0:
            text = f"Approaches {item['left']} and {item['right']} have no shared cases."
        elif not item["ids"]:
            text = f"Approaches {item['left']} and {item['right']} agree on every shared case."
        else:
            text = f"Approaches {item['left']} and {item['right']} disagree: {', '.join(item['ids'])}."
        lines.append(f"<p>{escape(text)}</p>")
    return "<section id=\"disagreements\"><h2>Disagreeing cases</h2>" + "".join(lines) + "</section>"


def _case_table(view: dict) -> str:
    from html import escape

    rows = []
    for item in view["cases"]:
        latency = item["latency_ms"]
        haiku = item["haiku_usd"]
        rows.append(
            "<tr>"
            f"<td>{escape(str(item['approach']))}</td>"
            f"<td>{escape(str(item[CASE_ID]))}</td>"
            f"<td>{escape(str(item['final_class']))}</td>"
            f"<td>{escape(str(item['route']))}</td>"
            f"<td>{escape(_ms(latency if isinstance(latency, (int, float)) else None))}</td>"
            f"<td>{escape(_usd_text(haiku if isinstance(haiku, (int, float)) else None))}</td>"
            f"<td>{escape(_jev_text(item.get('jev_usd')))}</td>"
            f"<td>{'yes' if item.get('fallback_used') else 'no'}</td>"
            "</tr>"
        )
    if not rows:
        rows.append("<tr><td colspan=\"8\">No classification records.</td></tr>")
    return (
        "<section id=\"case-costs\"><h2>Per case</h2>"
        "<p class=\"note\">Fallback used names the answering model when the configured model could not answer.</p>"
        "<table><thead><tr><th>Approach</th><th>Case</th><th>Class</th><th>Route</th>"
        "<th>Latency ms</th><th>Haiku USD</th><th>Jev</th><th>Fallback used</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></section>"
    )


def _share(part: int, total: int) -> str:
    if total <= 0:
        return "—"
    return f"{part} of {total} ({part / total:.2f})"


def _pair(agree: int, shared: int) -> str:
    if shared <= 0:
        return "—"
    return f"{agree} / {shared}"


def _ms(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _cost(row: dict) -> str:
    if row["cases"] <= 0 or row["cost_per_case"] is None:
        return "—"
    amount = f"{row['cost_per_case']:.6f} USD"
    if row["priced_calls"] <= 0:
        return f"{amount}. No configured-model call"
    return amount


def _usd_text(value: float | None) -> str:
    if value is None:
        return "unpriced"
    return f"{value:.6f}"


def _jev_text(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "—"
    return f"{value:.6f}"


def _roots(*folders: Path | None) -> list[Path]:
    found = []
    for folder in folders:
        if folder is not None and folder not in found:
            found.append(folder)
    return found


def _safe(case_id: str) -> bool:
    if not case_id or case_id in {".", ".."} or ".." in case_id:
        return False
    return "/" not in case_id and "\\" not in case_id
