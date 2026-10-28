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
    runs = _read_runs(roots)
    view = build_comparison(
        runs,
        _read_feedback(roots),
        min_sample=settings.min_sample,
        gold=_load_gold(runs),
        official=_load_official(reports_dir),
        run_metrics=_load_run_metrics(reports_dir),
    )
    view["thresholds"] = {
        "false_negative_ceiling": settings.false_negative_ceiling,
        "false_positive_low": settings.false_positive_low,
        "false_positive_high": settings.false_positive_high,
    }
    return view


def build_comparison(
    runs: dict[str, dict[str, dict]],
    feedback: dict[str, str],
    *,
    min_sample: int,
    gold: dict[str, set[str]] | None = None,
    official: dict[str, dict] | None = None,
    run_metrics: dict[str, dict] | None = None,
) -> dict:
    """Summarize one row per approach. `runs` maps approach to case id to a safe row."""
    approaches = []
    labels = gold or {}
    reports = official or {}
    metrics = run_metrics or {}
    for name in _APPROACHES:
        rows = runs.get(name) or {}
        approaches.append(_approach(name, rows, feedback, min_sample, labels, reports.get(name) or {}, metrics.get(name) or {}))
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


def _approach(
    name: str,
    rows: dict[str, dict],
    feedback: dict[str, str],
    min_sample: int,
    gold: dict[str, set[str]],
    official: dict,
    run_metrics: dict,
) -> dict:
    ordered = [rows[key] for key in sorted(rows)]
    called = sum(1 for row in ordered if row.get("called_model") is True)
    manual = sum(1 for row in ordered if row.get("route") == Routing.MANUAL_REVIEW.value)
    latencies = [float(row["latency_ms"]) for row in ordered if isinstance(row.get("latency_ms"), (int, float))]
    priced = [
        float(row[key])
        for row in ordered
        for key in ("haiku_usd", "jev_usd")
        if isinstance(row.get(key), (int, float))
    ]
    paired = [row for row in ordered if row.get(CASE_ID) in feedback]
    wrong = sum(1 for row in paired if _label_wrong(row, feedback))
    ready = Routing.READY_FOR_REVIEW.value
    auto = [row for row in paired if row.get("route") == ready]
    auto_wrong = sum(1 for row in auto if _label_wrong(row, feedback))
    sample = len(paired)
    auto_sample = len(auto)
    cases = len(ordered)
    passed, passed_total, passed_source = _passed(ordered, gold, official)
    span = _span_metrics(run_metrics)
    if span["p95"] is not None:
        p95 = span["p95"]
        p95_source = "case"
    else:
        p95 = None if not latencies else round(percentile(latencies, 0.95) or 0, 3)
        p95_source = "audit"
    return {
        "name": name,
        "cases": cases,
        "called_model": called,
        "manual_count": manual,
        "p95": p95,
        "p95_source": p95_source,
        "priced_calls": len(priced),
        "cost_total": None if cases == 0 else round(sum(priced), 6),
        "cost_per_case": None if cases == 0 else round(sum(priced) / cases, 6),
        "passed": passed,
        "passed_total": passed_total,
        "passed_source": passed_source,
        "slo": span["slo"],
        "slo_target_ms": span["target_ms"],
        "slo_objective": span["objective"],
        "wrong": wrong,
        "sample": sample,
        "auto_wrong": auto_wrong,
        "auto_sample": auto_sample,
        "too_small": sample < min_sample,
        "auto_too_small": auto_sample < min_sample,
    }


def _passed(rows: list[dict], gold: dict[str, set[str]], official: dict) -> tuple[int | None, int | None, str]:
    passed_cases = official.get("passed_cases")
    total_cases = official.get("total_cases")
    if isinstance(passed_cases, int) and isinstance(total_cases, int) and total_cases >= 0:
        return passed_cases, total_cases, "eval"
    if not gold:
        return None, None, "none"
    by_id = {str(row.get(CASE_ID)): row for row in rows}
    hits = 0
    for case_id, labels in gold.items():
        row = by_id.get(case_id)
        if row is not None and row.get("final_class") in labels:
            hits += 1
    return hits, len(gold), "class"


def _span_metrics(run_metrics: dict) -> dict:
    inner = run_metrics.get("metrics") if isinstance(run_metrics.get("metrics"), dict) else {}
    latency = inner.get("case_latency_ms") if isinstance(inner.get("case_latency_ms"), dict) else {}
    p95 = latency.get("p95")
    rate = inner.get("good_case_rate")
    rules = run_metrics.get("slo") if isinstance(run_metrics.get("slo"), dict) else {}
    target = rules.get("latency_target_ms")
    objective = rules.get("objective")
    return {
        "p95": None if isinstance(p95, bool) or not isinstance(p95, (int, float)) else round(float(p95), 3),
        "slo": None if isinstance(rate, bool) or not isinstance(rate, (int, float)) else float(rate),
        "target_ms": 2000.0 if isinstance(target, bool) or not isinstance(target, (int, float)) else float(target),
        "objective": 0.95 if isinstance(objective, bool) or not isinstance(objective, (int, float)) else float(objective),
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
        "jev_usd": _usd(jev.get("cost") if isinstance(jev, dict) else None),
        "fallback_used": haiku.get("fallback_used") is True if isinstance(haiku, dict) else False,
    }


def _usd(cost: object) -> float | None:
    """A figure counts only when the saved status is the list-price label."""
    if not isinstance(cost, dict) or cost.get("status") != LIST_PRICE:
        return None
    value = cost.get("usd")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


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
        "<p class=\"lede\">Saved runs scored against the golden set. Each approach shows how many cases passed, list-price cost per case, case latency, and the SLO. This page has case ids and counts. It does not include complaint text.</p>",
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
        "<tr><th>Approach</th><th>Passed vs gold</th>"
        "<th>Cost</th><th>Latency p95</th><th>SLO</th></tr>"
    )
    body = []
    for row in view["approaches"]:
        body.append(
            "<tr>"
            f"<td>{escape(row['name'])}</td>"
            f"<td>{escape(_passed_text(row))}</td>"
            f"<td>{escape(_cost(row))}</td>"
            f"<td>{escape(_latency_cell(row['p95']))}</td>"
            f"<td>{escape(_slo_text(row))}</td>"
            "</tr>"
        )
    defined = (
        "<p class=\"note\">Passed vs gold is how many golden-set cases passed every automated check. "
        "Cost is the list-price USD for the configured model and Jev across every case run, with the per-case figure beside it. "
        "Jev is priced from the vendor list in config/rules/cost.yaml at 0.042 USD per million input tokens, and output tokens are free. "
        "A case with no priced call adds zero. "
        f"{_latency_note(view)}</p>"
    )
    return (
        "<section id=\"approaches\"><h2>Golden set</h2>"
        f"{defined}"
        f"<table><thead>{head}</thead><tbody>{''.join(body)}</tbody></table>"
        "</section>"
    )


def _passed_text(row: dict) -> str:
    passed = row.get("passed")
    total = row.get("passed_total")
    if not isinstance(passed, int) or not isinstance(total, int) or total < 0:
        return "—"
    return f"{passed} / {total}"


def _slo_text(row: dict) -> str:
    rate = row.get("slo")
    if isinstance(rate, bool) or not isinstance(rate, (int, float)):
        return "—"
    shown = f"{rate * 100:.0f}%"
    objective = row.get("slo_objective")
    target = row.get("slo_target_ms")
    if isinstance(objective, (int, float)) and not isinstance(objective, bool) and rate < float(objective):
        goal = f"{float(objective) * 100:.0f}%"
        return f"{shown}, below {goal}"
    if isinstance(target, (int, float)) and not isinstance(target, bool):
        return f"{shown} within {float(target):.0f} ms"
    return shown


def _latency_note(view: dict) -> str:
    rows = view.get("approaches") or []
    if any(row.get("p95_source") == "case" for row in rows):
        return (
            "Latency p95 is the case span. "
            "SLO is the share of cases drafted within the latency target. The objective is 95%."
        )
    if any(row.get("passed_source") == "class" for row in rows):
        return (
            "Passed counts a saved class that matches the gold class, because no eval report was found. "
            "Latency p95 is the classification audit. SLO needs the case-span metrics report."
        )
    return "Latency p95 is the classification audit. SLO needs the case-span metrics report."


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
        jev = item.get("jev_usd")
        rows.append(
            "<tr>"
            f"<td>{escape(str(item['approach']))}</td>"
            f"<td>{escape(str(item[CASE_ID]))}</td>"
            f"<td>{escape(str(item['final_class']))}</td>"
            f"<td>{escape(str(item['route']))}</td>"
            f"<td>{escape(_ms(latency if isinstance(latency, (int, float)) else None))}</td>"
            f"<td>{escape(_usd_text(haiku if isinstance(haiku, (int, float)) else None))}</td>"
            f"<td>{escape(_usd_text(jev if isinstance(jev, (int, float)) else None))}</td>"
            f"<td>{'yes' if item.get('fallback_used') else 'no'}</td>"
            "</tr>"
        )
    if not rows:
        rows.append("<tr><td colspan=\"8\">No classification records.</td></tr>")
    return (
        "<section id=\"case-costs\"><h2>Per case</h2>"
        "<p class=\"note\">Fallback used names the answering model when the configured model could not answer.</p>"
        "<table><thead><tr><th>Approach</th><th>Case</th><th>Class</th><th>Route</th>"
        "<th>Latency ms</th><th>Haiku USD</th><th>Jev USD</th><th>Fallback used</th></tr></thead>"
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


def _latency_cell(value: float | None) -> str:
    text = _ms(value)
    if text == "—":
        return text
    return f"{text} ms"


def _cost(row: dict) -> str:
    total = row.get("cost_total")
    per = row.get("cost_per_case")
    if row.get("cases", 0) <= 0 or not isinstance(total, (int, float)) or isinstance(total, bool):
        return "—"
    amount = f"{float(total):.6f} USD"
    if row.get("priced_calls", 0) <= 0:
        return f"{amount}. No priced call"
    if isinstance(per, bool) or not isinstance(per, (int, float)):
        return amount
    return f"{amount} ({float(per):.6f} per case)"


def _usd_text(value: float | None) -> str:
    if value is None:
        return "unpriced"
    return f"{value:.6f}"


def sibling_run_summaries(reports_dir: Path) -> list[dict]:
    """Per-approach case spans and golden-set passes stored beside a compare folder."""
    rows = []
    for approach, folder in _sibling_dirs(reports_dir).items():
        metrics = _read_json(folder / "metrics.json")
        official = _read_json(folder / "eval.json")
        span = _span_metrics(metrics)
        passed = official.get("passed_cases")
        total = official.get("total_cases")
        cases = metrics.get("metrics") if isinstance(metrics.get("metrics"), dict) else {}
        drafted = cases.get("cases_drafted")
        priced_total, priced_calls, audit_cases = _priced_total(folder, approach, reports_dir)
        denominator = audit_cases or (drafted if isinstance(drafted, int) else 0)
        rows.append(
            {
                "name": approach,
                "cases": drafted if isinstance(drafted, int) else None,
                "passed": passed if isinstance(passed, int) else None,
                "passed_total": total if isinstance(total, int) else None,
                "p95": span["p95"],
                "slo": span["slo"],
                "slo_target_ms": span["target_ms"],
                "slo_objective": span["objective"],
                "cost_total": round(priced_total, 6),
                "cost_per_case": None if denominator <= 0 else round(priced_total / denominator, 6),
                "priced_calls": priced_calls,
            }
        )
    return rows


def _load_gold(runs: dict[str, dict[str, dict]]) -> dict[str, set[str]]:
    ids = [case_id for name in _APPROACHES for case_id in (runs.get(name) or {})]
    root = Path(__file__).resolve().parents[3]
    path = root / "synthetic-data" / "seed_gold.json" if any(case_id.startswith("CARD-S") for case_id in ids) else root / "feedback" / "dev_gold.json"
    loaded = _read_json(path)
    labels: dict[str, set[str]] = {}
    for case_id, answer in loaded.items():
        if not isinstance(case_id, str) or not isinstance(answer, dict):
            continue
        raw = answer.get(CLASSIFICATION)
        if isinstance(raw, list):
            labels[case_id] = {item for item in raw if isinstance(item, str)}
    return labels


def _load_official(reports_dir: Path) -> dict[str, dict]:
    found = {}
    for approach, folder in _sibling_dirs(reports_dir).items():
        loaded = _read_json(folder / "eval.json")
        if isinstance(loaded.get("passed_cases"), int) and isinstance(loaded.get("total_cases"), int):
            found[approach] = {"passed_cases": loaded["passed_cases"], "total_cases": loaded["total_cases"]}
    return found


def _load_run_metrics(reports_dir: Path) -> dict[str, dict]:
    found = {}
    for approach, folder in _sibling_dirs(reports_dir).items():
        loaded = _read_json(folder / "metrics.json")
        if loaded:
            found[approach] = loaded
    return found


def _priced_total(run_dir: Path, approach: str, reports_dir: Path) -> tuple[float, int, int]:
    for folder in (run_dir / CLASSIFICATION / approach, reports_dir / CLASSIFICATION / approach):
        if not folder.is_dir():
            continue
        total = 0.0
        priced_calls = 0
        cases = 0
        for path in sorted(folder.glob("*.json")):
            row = _safe_row(path, path.stem)
            if row is None:
                continue
            cases += 1
            for key in ("haiku_usd", "jev_usd"):
                value = row.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                total += float(value)
                priced_calls += 1
        return total, priced_calls, cases
    return 0.0, 0, 0


def _sibling_dirs(reports_dir: Path) -> dict[str, Path]:
    name = reports_dir.name
    prefix = "compare-"
    if not name.startswith(prefix):
        return {}
    tag = name[len(prefix):]
    if not tag or tag in {".", ".."} or "/" in tag or "\\" in tag:
        return {}
    found = {}
    for approach in _APPROACHES:
        folder = reports_dir.parent / f"a{approach}-{tag}"
        if folder.is_dir():
            found[approach] = folder
    return found


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


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
