"""Read-only status page built from traces, JSON logs, and the cost file.

The page is a single HTML file. The server binds 127.0.0.1 and answers GET
only. It does not approve, reject, reset, or post.
"""

from __future__ import annotations

import json
import math
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dispute_copilot.evidence.redaction import Redactor
from dispute_copilot.fields import CASE_ID
from dispute_copilot.observability.cost import compute_cost, load_cost, price_call
from dispute_copilot.observability.metrics import compute, evaluate_alerts, load_slo, percentile, read_traces
from dispute_copilot.observability.path import PAGE_SCRIPT, build_paths, path_markup

CORE_SPANS = (
    "case",
    "intake.parse",
    "documents.followups",
    "rules.classify",
    "policy.select",
    "rules.routing",
    "citations.build",
    "ledger.lookup",
    "contract.validate",
    "redaction.check",
    "draft.write",
    "review.submit",
    "record.precheck",
    "record.create",
)
_LOG_LIMIT = 400
_TEMPLATES = "templates only (no model)"
_NOT_RECORDED = "not recorded"
_LIST_PRICE = "estimate from list price"
_HOST = "127.0.0.1"


def load_view(reports_dir: Path) -> dict:
    traces = reports_dir / "traces"
    records = read_traces(traces) if traces.is_dir() else []
    return build_view(records, _read_logs(reports_dir), audit=_read_audit(reports_dir / "audit.json"))


def build_view(
    records: list[dict],
    log_rows: list[dict],
    *,
    cost_table: dict | None = None,
    slo: dict | None = None,
    audit: list[dict] | None = None,
) -> dict:
    records = [item for item in records if isinstance(item, dict)]
    log_rows = [item for item in log_rows if isinstance(item, dict)]
    audit = audit or []
    table = cost_table if cost_table is not None else load_cost()
    rules = slo if slo is not None else load_slo()
    target = float(rules["slo"]["latency_target_ms"])
    metrics = compute(records, rules)
    spans = [item for item in records if item.get("kind") == "span"]
    events = [item for item in records if item.get("kind") == "event"]
    approval = _approval(records, audit)
    security = _security(events, log_rows)
    view = {
        "target_ms": target,
        "overview": _overview(spans, metrics, evaluate_alerts(metrics, rules), target),
        "waterfall": _waterfall(spans),
        "latency": _latency(spans, target),
        "cost": _cost(spans, table),
        "logs": _logs(log_rows, records),
        "security": security,
        "approval": approval,
        "deployment": _deployment(spans, table, target),
        "paths": build_paths(records, log_rows, approval),
    }
    return Redactor().scrub(view)


def render(view: dict) -> str:
    page = _page(view)
    redactor = Redactor()
    if not redactor.contains_restricted(page):
        return page
    masked = redactor.mask(page)
    if redactor.contains_restricted(masked):
        return "<!DOCTYPE html><title>redacted</title><p>redacted</p>"
    return masked


def write_html(reports_dir: Path, dest: Path, submissions_dir: Path | None = None) -> None:
    from dispute_copilot.observability.compare_page import load_comparison, render_comparison

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render(load_view(reports_dir)), encoding="utf-8")
    sibling = dest.with_name("compare.html")
    if sibling.resolve() == dest.resolve():
        sibling = dest.with_name("approach-compare.html")
    sibling.write_text(
        render_comparison(load_comparison(reports_dir, submissions_dir), back_href=dest.name),
        encoding="utf-8",
    )


def bind_page(html: str, port: int, compare_html: str | None = None) -> ThreadingHTTPServer:
    encoded = None if compare_html is None else compare_html.encode("utf-8")
    handler = _handler(html.encode("utf-8"), encoded)
    return ThreadingHTTPServer((_HOST, port), handler)


def _overview(spans: list[dict], metrics: dict, alerts: list[dict], target: float) -> dict:
    rows = []
    for span in spans:
        if span.get("name") != "case":
            continue
        attrs = _attrs(span)
        duration = _ms(span.get("duration_ms"))
        written = attrs.get("outcome") == "drafted"
        within = written and duration is not None and duration <= target
        rows.append(
            {
                "case": _case(span),
                "run": _run(span),
                "label": _label(span),
                "result": "pass" if within else "fail",
                "outcome": str(attrs.get("outcome") or "not written"),
                "duration_ms": duration,
                "route": str(attrs.get("route") or ""),
            }
        )
    rows.sort(key=lambda item: (item["case"], item["run"]))
    passed = sum(1 for item in rows if item["result"] == "pass")
    return {
        "rows": rows,
        "counts": {
            "cases": len(rows),
            "pass": passed,
            "fail": len(rows) - passed,
            "alerts": len(alerts),
        },
        "slo": {
            "target_ms": target,
            "objective": metrics.get("error_budget", {}).get("objective"),
            "good_case_rate": metrics.get("good_case_rate"),
            "cases_drafted": metrics.get("cases_drafted"),
            "cases_total": metrics.get("cases_total"),
        },
        "alerts": alerts,
        "latency_ms": metrics.get("case_latency_ms") or {},
    }


def _waterfall(spans: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for span in spans:
        grouped.setdefault(_case(span) or "(none)", []).append(span)
    blocks = []
    for case in sorted(grouped):
        rows = sorted(grouped[case], key=lambda item: (str(item.get("start") or item.get("ts") or ""), str(item.get("name"))))
        by_id = {item.get("span_id"): item for item in rows if item.get("span_id")}
        peak = max((_ms(item.get("duration_ms")) or 0) for item in rows) or 1
        drawn = []
        for item in rows:
            name = str(item.get("name") or "")
            duration = _ms(item.get("duration_ms"))
            drawn.append(
                {
                    "name": name,
                    "core": name in CORE_SPANS,
                    "depth": _depth(item, by_id),
                    "duration_ms": duration,
                    "run": _run(item),
                    "width": 0 if duration is None else max(1.0, 100.0 * duration / peak),
                    "detail": _detail(item),
                }
            )
        blocks.append({"case": case, "rows": drawn})
    return blocks


def _latency(spans: list[dict], target: float) -> dict:
    samples: dict[str, list[float]] = {name: [] for name in CORE_SPANS}
    cases = []
    for span in spans:
        name = str(span.get("name") or "")
        duration = _ms(span.get("duration_ms"))
        if duration is None:
            continue
        samples.setdefault(name, []).append(duration)
        if name == "case":
            cases.append(
                {
                    "case": _case(span),
                    "run": _run(span),
                    "duration_ms": duration,
                    "target_ms": target,
                    "within_target": duration <= target and _attrs(span).get("outcome") == "drafted",
                }
            )
    steps = []
    names = list(CORE_SPANS) + sorted(name for name in samples if name not in CORE_SPANS)
    for name in names:
        values = samples.get(name) or []
        p50 = percentile(values, 0.50)
        p95 = percentile(values, 0.95)
        steps.append(
            {
                "name": name,
                "core": name in CORE_SPANS,
                "n": len(values),
                "p50": None if p50 is None else round(p50, 3),
                "p95": None if p95 is None else round(p95, 3),
                "max": None if not values else round(max(values), 3),
                "status": "measured" if values else "unmeasured",
            }
        )
    return {"by_step": steps, "by_case": cases, "target_ms": target}


def _cost(spans: list[dict], table: dict) -> dict:
    calls = []
    for span in spans:
        if span.get("name") != "model.call":
            continue
        attrs = _attrs(span)
        tokens_in = _tokens(attrs.get("tokens_in"))
        tokens_out = _tokens(attrs.get("tokens_out"))
        model = str(attrs.get("model") or "")
        if tokens_in is None or tokens_out is None:
            priced = {"status": "unmeasured", "usd": None}
        else:
            priced = price_call(model, tokens_in, tokens_out, table)
        calls.append(
            {
                "case": _case(span),
                "run": _run(span),
                "model": model or "(unnamed)",
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "status": priced["status"],
                "usd": priced["usd"],
            }
        )
    by_case: dict[str, list[dict]] = {}
    for call in calls:
        by_case.setdefault(call["case"] or "(none)", []).append(call)
    return {
        "calls": calls,
        "by_case": [{"case": case, **_rollup(rows)} for case, rows in sorted(by_case.items())],
        "run": _rollup(calls),
        "compute": compute_cost(table),
    }


def _logs(log_rows: list[dict], records: list[dict]) -> dict:
    source = log_rows if log_rows else records
    rows = [_log_row(item) for item in source]
    rows = [item for item in rows if item["event"] or item["text"]]
    hidden = max(0, len(rows) - _LOG_LIMIT)
    return {"rows": rows[:_LOG_LIMIT], "hidden": hidden, "total": len(rows)}


def _security(events: list[dict], log_rows: list[dict]) -> dict:
    injections = []
    refusals = []
    masked = []
    guards = []
    for item in events:
        name = str(item.get("name") or "")
        attrs = _attrs(item)
        if name in {"injection_detected", "security.untrusted_instruction"}:
            injections.append({"case": _case(item), "run": _run(item), "event": name, "detail": _short(attrs)})
        if name == "review.gate" and attrs.get("passed") is False:
            refusals.append({"case": _case(item), "run": _run(item), "event": name, "detail": _short(attrs)})
        if name.startswith("guardrail."):
            guards.append({"case": _case(item), "run": _run(item), "event": name, "detail": _short(attrs)})
        if _has_mask(item):
            masked.append({"case": _case(item), "run": _run(item), "event": name})
    for item in log_rows:
        if _has_mask(item):
            masked.append({"case": _case(item), "run": _run(item), "event": str(item.get("name") or "log")})
    return {"injections": injections, "refusals": refusals, "masked": _unique(masked), "guards": guards}


def _approval(records: list[dict], audit: list[dict]) -> list[dict]:
    cases: dict[str, dict] = {}

    def row(case: str) -> dict:
        return cases.setdefault(
            case,
            {"case": case, "checklist": "not run", "human": "unmeasured", "record": "not created"},
        )

    for item in records:
        case = _case(item)
        if not case:
            continue
        name = str(item.get("name") or "")
        attrs = _attrs(item)
        if name == "review.agent_checklist":
            row(case)["checklist"] = "passed" if attrs.get("passed") is True else "failed"
        if name == "record.create":
            row(case)["record"] = "created"
        if name == "case":
            row(case)
    for item in audit:
        case = _case(item)
        if not case:
            continue
        event = str(item.get("event") or "")
        if event == "human_approved":
            row(case)["human"] = "approved"
        elif event == "human_rejected":
            row(case)["human"] = "rejected"
        elif event == "review_requested" and row(case)["human"] == "unmeasured":
            row(case)["human"] = "waiting"
        elif event == "record_created":
            row(case)["record"] = "created"
    return [cases[key] for key in sorted(cases)]


def _deployment(spans: list[dict], table: dict, target: float) -> list[dict]:
    cases = [span for span in spans if span.get("name") == "case"]
    calls = [span for span in spans if span.get("name") == "model.call"]
    calls_by: dict[tuple[str, str], list[dict]] = {}
    for call in calls:
        calls_by.setdefault((_case(call), _run(call)), []).append(call)
    groups: dict[tuple[str, str], dict] = {}

    def group(where: str, model: str) -> dict:
        return groups.setdefault((where, model), {"cases": [], "calls": []})

    used: set[int] = set()
    for span in cases:
        where = _stored_where(span)
        related = calls_by.get((_case(span), _run(span)), [])
        if where is None:
            bucket = group(_label(span), _NOT_RECORDED)
            bucket["cases"].append(span)
            for call in related:
                if id(call) in used or _stored_where(call) is not None:
                    continue
                used.add(id(call))
                bucket["calls"].append(call)
            continue
        matched = []
        for call in related:
            if id(call) in used:
                continue
            if (_stored_where(call) or where) != where:
                continue
            used.add(id(call))
            matched.append(call)
        if not matched:
            group(where, _TEMPLATES)["cases"].append(span)
            continue
        seen: list[str] = []
        for call in matched:
            model = _model_name(call)
            if model not in seen:
                seen.append(model)
                group(where, model)["cases"].append(span)
            group(where, model)["calls"].append(call)
    for call in calls:
        if id(call) in used:
            continue
        where = _stored_where(call) or _label(call)
        model = _model_name(call) if _attrs(call).get("model") else _NOT_RECORDED
        group(where, model)["calls"].append(call)
    rows = []
    for (where, model), bucket in groups.items():
        items = bucket["cases"]
        durations = [value for value in (_ms(item.get("duration_ms")) for item in items) if value is not None]
        priced = [_priced_call(call, table) for call in bucket["calls"]]
        rolled = _rollup(priced)
        tokens_in = sum(item["tokens_in"] for item in priced)
        tokens_out = sum(item["tokens_out"] for item in priced)
        present = bool(items) or bool(bucket["calls"])
        rows.append(
            {
                "where": where,
                "model": model,
                "label": where,
                "cases": len(items),
                "passed": sum(1 for item in items if _passed(item, target)),
                "p50": None if not durations else round(percentile(durations, 0.50) or 0, 3),
                "p95": None if not durations else round(percentile(durations, 0.95) or 0, 3),
                "max": None if not durations else round(max(durations), 3),
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "measured": present,
                "status": "measured" if present else "unmeasured",
                "cost_status": rolled["status"] if priced else "unmeasured",
                "cost_usd": rolled["usd"] if priced else None,
            }
        )
    rows.sort(key=lambda item: (item["where"], item["model"]))
    return rows


def _page(view: dict) -> str:
    parts = [
        "<!DOCTYPE html>",
        "<html lang=\"en\"><head><meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        "<title>Run status</title>",
        f"<style>{_CSS}</style></head><body>",
        "<header><div class=\"top\"><p class=\"mark\">Read-only</p>"
        "<a class=\"mark\" href=\"compare.html\">Comparison</a>"
        "<button type=\"button\" id=\"theme-toggle\">Dark</button></div>",
        "<h1>Run status</h1>",
        "<p class=\"lede\">A read-only view of the runs already saved.</p>",
        "</header><main>",
        _headline(view),
        _section("", "Request path", "path", path_markup(view["paths"])),
        _summary_cards(view),
        "<details id=\"technical\"><summary>Technical details</summary><div class=\"tech\">",
        _section("", "Run overview", "overview", _alert_list(view["overview"]["alerts"]) + _overview_table(view["overview"]["rows"])),
        _section("", "Timeline", "timeline", _timeline(view["waterfall"])),
        _section("", "Latency", "latency", _latency_block(view["latency"])),
        _section("", "Cost", "cost", _cost_block(view["cost"])),
        _section("", "Logs", "logs", _log_block(view["logs"])),
        _section("", "Security", "security", _security_block(view["security"])),
        _section("", "Approval", "approval", _approval_block(view["approval"])),
        _section("", "Deployment", "deployment", _deployment_block(view["deployment"], view["cost"]["compute"])),
        "</div></details>",
        "</main>",
        PAGE_SCRIPT,
        "</body></html>",
    ]
    return "".join(parts)


def _headline(view: dict) -> str:
    counts = view["overview"]["counts"]
    rate = view["overview"]["slo"].get("good_case_rate")
    rate_text = "unmeasured" if rate is None else f"{rate:.4f}"
    flagged = len({item["case"] for item in view["security"]["injections"] if item.get("case")})
    refusals = len(view["security"]["refusals"])
    unsafe = sum(
        1
        for row in view["approval"]
        if row.get("record") == "created" and row.get("human") != "approved"
    )
    cells = [
        ("Cases run", str(counts["cases"])),
        ("Passed", str(counts["pass"])),
        ("Good-case rate", rate_text),
        ("Alerts", str(counts["alerts"])),
    ]
    stats = "".join(f"<div><p>{escape(label)}</p><strong>{escape(value)}</strong></div>" for label, value in cells)
    safety = (
        f"Injections flagged: {flagged}. Gate refusals: {refusals}. "
        f"Records created without approval: {unsafe}."
    )
    return (
        "<section id=\"headline\" class=\"strip\">"
        f"<div class=\"stats\">{stats}</div>"
        f"<p class=\"safety-line\">{escape(safety)}</p>"
        "<p class=\"note\">Passed means drafted inside the latency target.</p>"
        "</section>"
    )


def _summary_cards(view: dict) -> str:
    return (
        "<section id=\"summaries\" class=\"summary-grid\">"
        + _latency_card(view)
        + _cost_card(view["cost"])
        + _safety_card(view)
        + _where_card(view["deployment"])
        + "</section>"
    )


def _latency_card(view: dict) -> str:
    latency = view["latency"]
    timing = view["overview"].get("latency_ms") or {}
    p50 = _num(timing.get("median"))
    p95 = _num(timing.get("p95"))
    slow = _slowest_line(view)
    chart = _svg_bars(
        [(item["case"], item["duration_ms"]) for item in latency["by_case"]],
        target=None,
        unit="ms",
        scale="log",
        chart_id="case-chart",
    )
    return (
        "<article id=\"card-latency\"><h2>Latency</h2>"
        f"<p class=\"note\">p50 {escape(p50)} ms. p95 {escape(p95)} ms. {escape(slow)}</p>"
        f"{chart}</article>"
    )


def _slowest_line(view: dict) -> str:
    cases = view["latency"]["by_case"]
    if not cases:
        return "No case timing was recorded."
    slow = max(cases, key=lambda item: item["duration_ms"] or 0)
    reason = "it was the longest draft"
    for path in view["paths"]:
        if path["case"] != slow["case"]:
            continue
        for node in path["nodes"]:
            dependency = node["attrs"].get("dependency")
            if "fail" not in node["marks"]:
                continue
            if dependency == "transactions":
                reason = "the transaction check failed"
            elif dependency == "merchant" and reason == "it was the longest draft":
                reason = "the merchant check failed"
    return f"{slow['case']} took {_num(slow['duration_ms'])} ms because {reason}."


def _cost_card(cost: dict) -> str:
    run = cost["run"]
    if not run.get("calls"):
        return (
            "<article id=\"card-cost\"><h2>Cost</h2>"
            "<p class=\"note\">No model call was recorded. "
            "Prices per million tokens are in config/rules/cost.yaml. "
            "A listed model is an estimate from list price.</p></article>"
        )
    bits = [f"{item['case']} {_money(item)}" for item in cost["by_case"]]
    per_case = ". ".join(bits) if bits else "No per-case figure."
    return (
        "<article id=\"card-cost\"><h2>Cost</h2>"
        f"<p class=\"note\">{escape(_money(run))}. Per case: {escape(per_case)}</p></article>"
    )


def _safety_card(view: dict) -> str:
    rows = view["approval"]
    passed = sum(1 for row in rows if row.get("checklist") == "passed")
    humans = {row.get("human") for row in rows}
    if humans == {"unmeasured"} or not humans:
        human_line = "Human approval is unmeasured."
    else:
        approved = sum(1 for row in rows if row.get("human") == "approved")
        human_line = f"Human approval recorded for {approved} of {len(rows)}."
    flagged = len({path["case"] for path in view["paths"] if path.get("injection")})
    treated = (
        "1 case treated untrusted text as data."
        if flagged == 1
        else f"{flagged} cases treated untrusted text as data."
    )
    line = f"{passed} of {len(rows)} checklists passed. {human_line} {treated}"
    return (
        "<article id=\"card-safety\"><h2>Safety and approval</h2>"
        f"<p class=\"note\">{escape(line)}</p></article>"
    )


def _where_card(rows: list[dict]) -> str:
    body = []
    for item in rows:
        body.append(
            "<tr>"
            f"<td>{escape(item['where'])}</td>"
            f"<td>{escape(item['model'])}</td>"
            f"<td class=\"num\">{item['cases']}</td>"
            f"<td class=\"num\">{item['passed']}</td>"
            f"<td class=\"num\">{escape(_num(item['p50']))}</td>"
            f"<td class=\"num\">{escape(_num(item['p95']))}</td>"
            f"<td class=\"num\">{item['tokens_in']}</td>"
            f"<td class=\"num\">{item['tokens_out']}</td>"
            f"<td>{escape(_priced(item))}</td>"
            "</tr>"
        )
    empty = '<tr><td colspan="9">No runs loaded.</td></tr>'
    table = (
        "<table><thead><tr><th>Place</th><th>Model</th><th>Cases</th><th>Passed</th>"
        "<th>p50</th><th>p95</th><th>Tokens in</th><th>Tokens out</th><th>Cost</th></tr></thead>"
        f"<tbody>{''.join(body) or empty}</tbody></table>"
    )
    places = {item["where"] for item in rows}
    extra = ""
    if len(places) == 1:
        extra = "<p class=\"note muted\">Other environments appear when their runs are loaded.</p>"
    return (
        "<article id=\"card-where\"><h2>Where it runs</h2>"
        "<p class=\"note\">One row for each place and model in the saved runs.</p>"
        f"{table}{extra}</article>"
    )


def _section(number: str, title: str, anchor: str, body: str) -> str:
    prefix = f"<span class=\"num\">{escape(number)}</span> " if number else ""
    return f"<section id=\"{anchor}\"><h2>{prefix}{escape(title)}</h2>{body}</section>"


def _stat_row(counts: dict, slo: dict) -> str:
    rate = slo.get("good_case_rate")
    rate_text = "unmeasured" if rate is None else f"{rate:.4f}"
    cells = [
        ("Cases", str(counts["cases"])),
        ("Pass", str(counts["pass"])),
        ("Fail", str(counts["fail"])),
        ("Alerts", str(counts["alerts"])),
        ("Good-case rate", rate_text),
        ("SLO target", f"{_num(slo.get('target_ms'))} ms"),
    ]
    body = "".join(f"<div><p>{escape(label)}</p><strong>{escape(value)}</strong></div>" for label, value in cells)
    return f"<div class=\"stats\">{body}</div><p class=\"note\">A pass means the case was written inside the latency target. It is not a gold score and it is not an approval.</p>"


def _alert_list(alerts: list[dict]) -> str:
    if not alerts:
        return "<p class=\"ok\">No alerts fired.</p>"
    items = "".join(
        f"<li><strong>{escape(str(item.get('alert')))}</strong> "
        f"{escape(str(item.get('severity')))} "
        f"{escape(str(item.get('metric')))}={escape(str(item.get('value')))} "
        f"({escape(str(item.get('threshold')))})</li>"
        for item in alerts
    )
    return f"<ul class=\"alerts\">{items}</ul>"


def _overview_table(rows: list[dict]) -> str:
    if not rows:
        return "<p class=\"empty\">No case spans in this directory.</p>"
    body = []
    for item in rows:
        kind = "pass" if item["result"] == "pass" else "fail"
        body.append(
            "<tr>"
            f"<td>{escape(item['case'])}</td>"
            f"<td>{escape(item['run'])}</td>"
            f"<td>{escape(item['label'])}</td>"
            f"<td class=\"{kind}\">{escape(item['result'])}</td>"
            f"<td>{escape(item['outcome'])}</td>"
            f"<td class=\"num\">{escape(_num(item['duration_ms']))}</td>"
            f"<td>{escape(item['route'])}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>Case</th><th>Run</th><th>Label</th><th>Result</th>"
        "<th>Outcome</th><th>ms</th><th>Route</th></tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table>"
    )


def _timeline(blocks: list[dict]) -> str:
    if not blocks:
        return "<p class=\"empty\">No spans.</p>"
    parts = [
        "<p class=\"note\">Core steps are the fourteen names this program records for a case, "
        "including review and record steps when those commands ran. Other spans sit under them.</p>"
    ]
    for index, block in enumerate(blocks):
        open_attr = " open" if index == 0 else ""
        rows = []
        for item in block["rows"]:
            mark = "core" if item["core"] else "sub"
            pad = item["depth"] * 16
            width = f"{item['width']:.1f}"
            rows.append(
                f"<div class=\"step {mark}\" style=\"margin-left:{pad}px\">"
                f"<span class=\"step-name\">{escape(item['name'])}</span>"
                f"<span class=\"track\"><span class=\"fill\" style=\"width:{width}%\"></span></span>"
                f"<span class=\"ms\">{escape(_num(item['duration_ms']))}</span>"
                f"<span class=\"detail\">{escape(item['detail'])}</span>"
                "</div>"
            )
        parts.append(
            f"<details{open_attr}><summary>{escape(block['case'])} "
            f"<em>{len(block['rows'])} spans</em></summary>{''.join(rows)}</details>"
        )
    return "".join(parts)


def _latency_block(latency: dict) -> str:
    steps = latency["by_step"]
    chart = _svg_bars(
        [(item["name"], item["p95"]) for item in steps if item["core"] or item["status"] == "measured"],
        target=None,
        unit="ms",
        scale="log",
        chart_id="step-chart",
    )
    head = (
        f"<p class=\"note\">p50, p95, and max use the nearest rank above the fraction "
        f"(the same rank as the metrics report). SLO target {_num(latency['target_ms'])} ms. "
        "A step with no samples is unmeasured. The chart uses a log scale so a short step "
        "stays visible beside a long one. The target is named here rather than drawn across those steps.</p>"
    )
    body = []
    for item in steps:
        body.append(
            "<tr>"
            f"<td>{escape(item['name'])}</td>"
            f"<td>{'core' if item['core'] else 'sub'}</td>"
            f"<td class=\"num\">{item['n']}</td>"
            f"<td class=\"num\">{escape(_num(item['p50']))}</td>"
            f"<td class=\"num\">{escape(_num(item['p95']))}</td>"
            f"<td class=\"num\">{escape(_num(item['max']))}</td>"
            f"<td>{escape(item['status'])}</td>"
            "</tr>"
        )
    table = (
        "<table><thead><tr><th>Step</th><th>Kind</th><th>n</th><th>p50</th><th>p95</th>"
        f"<th>max</th><th>Status</th></tr></thead><tbody>{''.join(body)}</tbody></table>"
    )
    case_rows = []
    for item in latency["by_case"]:
        flag = "within target" if item["within_target"] else "over target"
        case_rows.append(
            "<tr>"
            f"<td>{escape(item['case'])}</td>"
            f"<td class=\"num\">{escape(_num(item['duration_ms']))}</td>"
            f"<td class=\"num\">{escape(_num(item['target_ms']))}</td>"
            f"<td>{flag}</td></tr>"
        )
    cases = (
        "<h3>Per case</h3><table><thead><tr><th>Case</th><th>ms</th><th>Target</th><th>Status</th></tr></thead>"
        f"<tbody>{''.join(case_rows) or '<tr><td colspan=\"4\">unmeasured</td></tr>'}</tbody></table>"
    )
    return head + chart + table + cases


def _cost_block(cost: dict) -> str:
    compute = cost["compute"]
    run = cost["run"]
    summary = (
        "<div class=\"stats\">"
        f"<div><p>Run</p><strong>{escape(_money(run))}</strong></div>"
        f"<div><p>Model calls</p><strong>{run['calls']}</strong></div>"
        f"<div><p>Compute</p><strong>{escape(_money(compute))}</strong></div>"
        "</div>"
        "<p class=\"note\">Per-call figures use config/rules/cost.yaml, USD per million tokens. "
        "A listed model is an estimate from list price. "
        "A model with no entry names that model. "
        "Unmeasured means the tokens were not recorded. "
        "A total is a number only when every included call has a list price.</p>"
    )
    chart_items = [(item["model"], item["usd"]) for item in cost["calls"] if item["status"] == _LIST_PRICE]
    chart = _svg_bars(chart_items, target=None, unit="usd") if chart_items else "<p class=\"empty\">No priced model call.</p>"
    body = []
    for item in cost["calls"]:
        body.append(
            "<tr>"
            f"<td>{escape(item['case'])}</td>"
            f"<td>{escape(item['model'])}</td>"
            f"<td class=\"num\">{escape(_num(item['tokens_in']))}</td>"
            f"<td class=\"num\">{escape(_num(item['tokens_out']))}</td>"
            f"<td>{escape(_money(item))}</td>"
            "</tr>"
        )
    calls = (
        "<table><thead><tr><th>Case</th><th>Model</th><th>In</th><th>Out</th><th>Cost</th></tr></thead>"
        f"<tbody>{''.join(body) or '<tr><td colspan=\"5\">unmeasured</td></tr>'}</tbody></table>"
    )
    case_rows = "".join(
        f"<tr><td>{escape(item['case'])}</td><td>{escape(_money(item))}</td><td class=\"num\">{item['calls']}</td></tr>"
        for item in cost["by_case"]
    )
    by_case = (
        "<h3>Per case</h3><table><thead><tr><th>Case</th><th>Cost</th><th>Calls</th></tr></thead>"
        f"<tbody>{case_rows or '<tr><td colspan=\"3\">unmeasured</td></tr>'}</tbody></table>"
    )
    return summary + chart + calls + by_case


def _log_block(logs: dict) -> str:
    note = (
        f"<p class=\"note\" id=\"log-count\" data-total=\"{logs['total']}\" "
        f"data-hidden=\"{logs['hidden']}\" data-limit=\"{_LOG_LIMIT}\">"
        f"{logs['total']} lines"
    )
    if logs["hidden"]:
        note += f", showing {_LOG_LIMIT}, {logs['hidden']} older lines left out"
    note += ".</p>"
    controls = (
        "<div class=\"filters\">"
        "<input id=\"f-run\" placeholder=\"run\" aria-label=\"run\">"
        "<input id=\"f-case\" placeholder=\"case\" aria-label=\"case\">"
        "<input id=\"f-span\" placeholder=\"span\" aria-label=\"span\">"
        "<input id=\"f-event\" placeholder=\"event\" aria-label=\"event\">"
        "<select id=\"f-level\" aria-label=\"level\">"
        "<option value=\"\">level</option><option>info</option><option>warning</option><option>error</option>"
        "</select></div>"
    )
    body = []
    for item in logs["rows"]:
        body.append(
            "<tr"
            f" data-run=\"{escape(item['run'], quote=True)}\""
            f" data-case=\"{escape(item['case'], quote=True)}\""
            f" data-span=\"{escape(item['span'], quote=True)}\""
            f" data-level=\"{escape(item['level'], quote=True)}\""
            f" data-event=\"{escape(item['event'], quote=True)}\">"
            f"<td>{escape(item['level'])}</td>"
            f"<td>{escape(item['run'])}</td>"
            f"<td>{escape(item['case'])}</td>"
            f"<td>{escape(item['span'])}</td>"
            f"<td>{escape(item['event'])}</td>"
            f"<td>{escape(item['text'])}</td></tr>"
        )
    table = (
        "<div class=\"log-scroll\"><table id=\"log-table\"><thead><tr><th>Level</th><th>Run</th><th>Case</th>"
        f"<th>Span</th><th>Event</th><th>Detail</th></tr></thead><tbody>{''.join(body)}</tbody></table></div>"
    )
    return note + controls + table


def _security_block(security: dict) -> str:
    return (
        _named_table("Flagged injections", security["injections"], with_detail=True)
        + _named_table("Gate refusals", security["refusals"], with_detail=True)
        + _named_table("Masked values", security["masked"], with_detail=False)
        + _named_table("Guardrails", security.get("guards") or [], with_detail=True)
    )


def _approval_block(rows: list[dict]) -> str:
    note = (
        "<p class=\"note\">Checklist result, human review state, and whether a record was created. "
        "Human state stays unmeasured unless an audit file is present. This view cannot change it.</p>"
    )
    if not rows:
        return note + "<p class=\"empty\">No cases.</p>"
    body = []
    for item in rows:
        body.append(
            "<tr>"
            f"<td>{escape(item['case'])}</td>"
            f"<td>{escape(item['checklist'])}</td>"
            f"<td>{escape(item['human'])}</td>"
            f"<td>{escape(item['record'])}</td>"
            "</tr>"
        )
    return (
        note
        + "<table><thead><tr><th>Case</th><th>Checklist</th><th>Human review</th><th>Record</th></tr></thead>"
        + f"<tbody>{''.join(body)}</tbody></table>"
    )


def _deployment_block(rows: list[dict], compute: dict) -> str:
    note = (
        "<p class=\"note\">Each row is a place and a model found in the saved runs. "
        "A run with no model call is templates only. "
        "An older run that did not store a place is grouped by its label, and the model reads not recorded. "
        f"Compute for this file: {escape(_money(compute))}.</p>"
    )
    chart = _svg_bars(
        [(f"{item['where']} / {item['model']}", item["p95"]) for item in rows if item["p95"] is not None],
        target=None,
        unit="ms",
    )
    body = []
    for item in rows:
        body.append(
            "<tr>"
            f"<td>{escape(item['where'])}</td>"
            f"<td>{escape(item['model'])}</td>"
            f"<td class=\"num\">{item['cases']}</td>"
            f"<td class=\"num\">{item['passed']}</td>"
            f"<td class=\"num\">{escape(_num(item['p50']))}</td>"
            f"<td class=\"num\">{escape(_num(item['p95']))}</td>"
            f"<td class=\"num\">{item['tokens_in']}</td>"
            f"<td class=\"num\">{item['tokens_out']}</td>"
            f"<td>{escape(_priced(item))}</td>"
            f"<td>{escape(item['status'])}</td>"
            "</tr>"
        )
    table = (
        "<table><thead><tr><th>Place</th><th>Model</th><th>Cases</th><th>Passed</th>"
        "<th>p50</th><th>p95</th><th>Tokens in</th><th>Tokens out</th><th>Cost</th>"
        f"<th>Status</th></tr></thead><tbody>{''.join(body)}</tbody></table>"
    )
    return note + (chart or "<p class=\"empty\">No saved runs to chart.</p>") + table


def _named_table(title: str, rows: list[dict], *, with_detail: bool) -> str:
    if not rows:
        return f"<h3>{escape(title)}</h3><p class=\"empty\">None.</p>"
    body = []
    for item in rows:
        detail = f"<td>{escape(item.get('detail') or '')}</td>" if with_detail else ""
        body.append(
            "<tr>"
            f"<td>{escape(item.get('case') or '')}</td>"
            f"<td>{escape(item.get('event') or '')}</td>"
            f"{detail}</tr>"
        )
    detail_head = "<th>Detail</th>" if with_detail else ""
    return (
        f"<h3>{escape(title)}</h3><table><thead><tr><th>Case</th><th>Event</th>{detail_head}</tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table>"
    )


def _svg_bars(
    items: list[tuple[str, float | None]],
    *,
    target: float | None,
    unit: str,
    scale: str = "linear",
    chart_id: str = "",
) -> str:
    measured = [(name, value) for name, value in items if isinstance(value, (int, float)) and not isinstance(value, bool)]
    if not measured:
        return ""
    peak = max(value for _, value in measured) or 1
    positive = [value for _, value in measured if value > 0]
    floor = min(positive) if positive else peak
    if scale != "log" and target and target <= peak * 3:
        peak = max(peak, target)
    row_h = 22
    label_w = 168
    chart_w = 360
    height = row_h * len(items) + 10
    width = label_w + chart_w + 92
    parts = [
        f'<svg class="chart" id="{escape(chart_id)}" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(unit)}">',
    ]
    for index, (name, value) in enumerate(items):
        y = 6 + index * row_h
        parts.append(
            f'<text x="0" y="{y + 12}" fill="currentColor" font-size="11">{escape(name)}</text>'
        )
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            parts.append(f'<text x="{label_w}" y="{y + 12}" fill="currentColor" font-size="11">unmeasured</text>')
            continue
        bar = _bar_length(float(value), peak, chart_w, scale, floor)
        parts.append(
            f'<rect x="{label_w}" y="{y}" width="{bar:.2f}" height="14" rx="2" fill="currentColor"></rect>'
        )
        parts.append(
            f'<text x="{label_w + bar + 6:.1f}" y="{y + 12}" fill="currentColor" font-size="11">{value:.3f}</text>'
        )
    if scale != "log" and target and target <= peak * 3:
        x = label_w + chart_w * (target / peak)
        parts.append(f'<line x1="{x:.2f}" y1="0" x2="{x:.2f}" y2="{height}" stroke="currentColor" stroke-dasharray="3 3"></line>')
    parts.append("</svg>")
    return "".join(parts)


def _bar_length(value: float, peak: float, chart_w: float, scale: str, floor: float) -> float:
    if scale != "log":
        return chart_w * (value / peak) if peak else 0
    if value <= 0 or floor <= 0 or peak <= 0:
        return chart_w * 0.18
    lo = math.log10(floor)
    hi = math.log10(peak)
    span = hi - lo
    if span <= 0:
        return chart_w
    position = (math.log10(value) - lo) / span
    return chart_w * (0.18 + 0.82 * position)


def _log_row(item: dict) -> dict:
    name = str(item.get("name") or "")
    kind = str(item.get("kind") or "")
    attrs = _attrs(item)
    message = item.get("message")
    text = message.strip() if isinstance(message, str) else _short(attrs)
    return {
        "run": _run(item),
        "case": _case(item),
        "span": name if kind == "span" else str(item.get("span") or ""),
        "level": str(item.get("level") or "info"),
        "event": name,
        "text": text[:500],
    }


def _handler(page: bytes, compare: bytes | None = None):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in {"/", "/index.html"}:
                self._send(200, "text/html; charset=utf-8", page)
                return
            if path in {"/compare", "/compare.html"} and compare is not None:
                self._send(200, "text/html; charset=utf-8", compare)
                return
            if path == "/health":
                self._send(200, "application/json", b'{"status":"ok","read_only":true}')
                return
            self._send(404, "application/json", b'{"error":"not found"}')

        def do_POST(self) -> None:  # noqa: N802
            self._refuse()

        def do_PUT(self) -> None:  # noqa: N802
            self._refuse()

        def do_PATCH(self) -> None:  # noqa: N802
            self._refuse()

        def do_DELETE(self) -> None:  # noqa: N802
            self._refuse()

        def _refuse(self) -> None:
            self._send(405, "application/json", b'{"error":"read-only"}', allow="GET")

        def _send(self, status: int, content_type: str, body: bytes, allow: str | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if allow:
                self.send_header("Allow", allow)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args) -> None:
            return None

    return Handler


def _read_logs(reports_dir: Path) -> list[dict]:
    rows: list[dict] = []
    folder = reports_dir / "logs"
    for name in ("run.jsonl", "mcp.jsonl"):
        path = folder / name
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
    return rows


def _read_audit(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(loaded, list):
        return []
    return [item for item in loaded if isinstance(item, dict)]


def _rollup(calls: list[dict]) -> dict:
    if not calls:
        return {"status": "unmeasured", "usd": None, "calls": 0}
    statuses = [str(item["status"]) for item in calls]
    if all(status == _LIST_PRICE for status in statuses):
        total = round(sum(float(item["usd"] or 0) for item in calls), 6)
        return {"status": _LIST_PRICE, "usd": total, "calls": len(calls)}
    missing = []
    for status in statuses:
        if status.startswith("no price configured for ") and status not in missing:
            missing.append(status)
    if missing and len(missing) == len(statuses):
        status = missing[0] if len(missing) == 1 else "; ".join(missing)
        return {"status": status, "usd": None, "calls": len(calls)}
    if missing:
        return {"status": missing[0] if len(missing) == 1 else "; ".join(missing), "usd": None, "calls": len(calls)}
    return {"status": "unmeasured", "usd": None, "calls": len(calls)}


def _attrs(item: dict) -> dict:
    attrs = item.get("attrs")
    return attrs if isinstance(attrs, dict) else {}


def _case(item: dict) -> str:
    value = item.get(CASE_ID)
    return value if isinstance(value, str) else ""


def _run(item: dict) -> str:
    value = item.get("run_id")
    return value if isinstance(value, str) else ""


def _label(item: dict) -> str:
    value = item.get("label")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "unlabeled"


def _ms(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _tokens(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0:
        return None
    return value


def _depth(item: dict, by_id: dict, guard: int = 0) -> int:
    if guard > 8:
        return 8
    parent = item.get("parent_id")
    if not parent or parent not in by_id:
        return 0
    return min(8, 1 + _depth(by_id[parent], by_id, guard + 1))


def _detail(item: dict) -> str:
    attrs = _attrs(item)
    keys = (
        "outcome",
        "route",
        "dependency",
        "attempts",
        "attempt",
        "http_status",
        "model",
        "tokens_in",
        "tokens_out",
        "cost_status",
        "decision",
    )
    bits = [f"{key}={attrs[key]}" for key in keys if key in attrs and attrs[key] not in (None, "")]
    return " ".join(bits)[:180]


def _short(attrs: dict) -> str:
    bits = []
    for key in sorted(attrs):
        value = attrs[key]
        if isinstance(value, (dict, list)):
            continue
        bits.append(f"{key}={value}")
    return " ".join(bits)[:240]


def _has_mask(item: dict) -> bool:
    try:
        text = json.dumps(item, ensure_ascii=False)
    except (TypeError, ValueError):
        return False
    return "[redacted]" in text


def _unique(rows: list[dict]) -> list[dict]:
    seen = set()
    kept = []
    for item in rows:
        key = (item.get("case"), item.get("run"), item.get("event"))
        if key in seen:
            continue
        seen.add(key)
        kept.append(item)
    return kept


def _money(item: dict) -> str:
    status = str(item.get("status") or "unmeasured")
    usd = item.get("usd")
    if isinstance(usd, bool) or not isinstance(usd, (int, float)):
        return status
    if status == "provided":
        return f"{usd:.6f}"
    if status == _LIST_PRICE:
        return f"{usd:.6f} estimate from list price"
    return status


def _priced(row: dict) -> str:
    return _money({"status": row.get("cost_status"), "usd": row.get("cost_usd")})


def _priced_call(call: dict, table: dict) -> dict:
    attrs = _attrs(call)
    tokens_in = _tokens(attrs.get("tokens_in"))
    tokens_out = _tokens(attrs.get("tokens_out"))
    model = str(attrs.get("model") or "")
    if tokens_in is None or tokens_out is None:
        return {"status": "unmeasured", "usd": None, "tokens_in": 0, "tokens_out": 0}
    priced = price_call(model, tokens_in, tokens_out, table)
    return {**priced, "tokens_in": tokens_in, "tokens_out": tokens_out}


def _passed(span: dict, target: float) -> bool:
    duration = _ms(span.get("duration_ms"))
    return _attrs(span).get("outcome") == "drafted" and duration is not None and duration <= target


def _stored_where(span: dict) -> str | None:
    value = _attrs(span).get("where")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _model_name(span: dict) -> str:
    value = _attrs(span).get("model")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "(unnamed)"


def _num(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value)


_CSS = """
:root {
  color-scheme: light;
  --bg: #f4f6f7;
  --bg-soft: #e8eef0;
  --ink: #1a1f22;
  --ink-soft: #5a656c;
  --ink-faint: #8a969d;
  --line: rgba(26,31,34,0.12);
  --accent: #2f5f63;
  --accent-soft: #c5d5d7;
  --card: #fbfcfc;
  --shadow: 0 30px 60px rgba(26,31,34,0.12);
  --focus: rgba(47,95,99,0.45);
  --warn: #7a4e24;
  --warn-bg: #f4ecdf;
  --bad: #7a332e;
  --bad-bg: #f6e7e4;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --bg: #121618;
    --bg-soft: #1a2022;
    --ink: #e8ecee;
    --ink-soft: #a3adb3;
    --ink-faint: #7a858c;
    --line: rgba(232,236,238,0.12);
    --accent: #7eb0b4;
    --accent-soft: #2a4548;
    --card: #1a1f22;
    --shadow: 0 30px 60px rgba(0,0,0,0.45);
    --focus: rgba(126,176,180,0.5);
    --warn: #e0c09a;
    --warn-bg: #2a241c;
    --bad: #e7b1a8;
    --bad-bg: #2c211f;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #121618;
  --bg-soft: #1a2022;
  --ink: #e8ecee;
  --ink-soft: #a3adb3;
  --ink-faint: #7a858c;
  --line: rgba(232,236,238,0.12);
  --accent: #7eb0b4;
  --accent-soft: #2a4548;
  --card: #1a1f22;
  --shadow: 0 30px 60px rgba(0,0,0,0.45);
  --focus: rgba(126,176,180,0.5);
  --warn: #e0c09a;
  --warn-bg: #2a241c;
  --bad: #e7b1a8;
  --bad-bg: #2c211f;
}
:root[data-theme="light"] {
  color-scheme: light;
  --bg: #f4f6f7;
  --bg-soft: #e8eef0;
  --ink: #1a1f22;
  --ink-soft: #5a656c;
  --ink-faint: #8a969d;
  --line: rgba(26,31,34,0.12);
  --accent: #2f5f63;
  --accent-soft: #c5d5d7;
  --card: #fbfcfc;
  --shadow: 0 30px 60px rgba(26,31,34,0.12);
  --focus: rgba(47,95,99,0.45);
  --warn: #7a4e24;
  --warn-bg: #f4ecdf;
  --bad: #7a332e;
  --bad-bg: #f6e7e4;
}
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:17px/1.6 "Inter", system-ui, sans-serif; }
header, main { width:min(1120px, calc(100% - 48px)); margin:0 auto; }
header { padding:48px 0 8px; }
.top { display:flex; justify-content:space-between; align-items:center; gap:16px; }
.mark { letter-spacing:.16em; text-transform:uppercase; font-size:12px; color:var(--accent); margin:0; }
a.mark { text-decoration:none; }
#theme-toggle, .replay button, .path-picks select, .filters input, .filters select {
  font:inherit; color:var(--ink); background:var(--card); border:1px solid var(--line); border-radius:999px;
}
#theme-toggle { padding:6px 14px; }
h1, h2, h3 { font-family:"Instrument Serif", Georgia, serif; font-weight:400; }
h1 { font-size:52px; line-height:1.05; margin:18px 0 12px; }
.lede { max-width:62ch; color:var(--ink-soft); margin:0 0 28px; }
main { display:grid; gap:28px; padding-bottom:64px; min-width:0; }
section { background:var(--card); border:1px solid var(--line); border-radius:20px; padding:28px 32px 32px; box-shadow:var(--shadow); min-width:0; }
#headline, #summaries { background:transparent; border:0; box-shadow:none; padding:0; }
.safety-line { margin:16px 0 0; }
.summary-grid { display:grid; grid-template-columns:1fr 1fr; gap:18px; }
.summary-grid article { background:var(--card); border:1px solid var(--line); border-radius:20px; padding:22px 24px 18px; box-shadow:var(--shadow); }
.summary-grid h2 { font-size:28px; }
#technical { background:transparent; border:0; box-shadow:none; padding:0 0 12px; }
#technical > summary { font-family:"Instrument Serif", Georgia, serif; font-size:32px; font-weight:400; cursor:pointer; width:max-content; }
.tech { display:grid; gap:28px; margin-top:18px; }
h2 { font-size:32px; line-height:1.15; margin:0 0 18px; }
h3 { font-size:22px; margin:28px 0 10px; }
.num { color:var(--accent); margin-right:10px; }
.stats { display:grid; grid-template-columns:repeat(auto-fit, minmax(140px, 1fr)); gap:14px; }
.stats div { background:var(--bg-soft); border-radius:16px; padding:14px 16px; }
.stats p { margin:0; color:var(--ink-soft); font-size:12px; text-transform:uppercase; letter-spacing:.06em; }
.stats strong { display:block; margin-top:6px; font-family:"Instrument Serif", Georgia, serif; font-size:32px; font-weight:400; }
.note, .empty { color:var(--ink-soft); }
.note.muted { font-size:13px; }
.ok { color:var(--accent); }
.alerts { margin:12px 0; padding-left:18px; color:var(--warn); }
table { width:100%; border-collapse:collapse; font-size:14px; font-variant-numeric:tabular-nums; }
th, td { text-align:left; padding:10px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
th { color:var(--ink-soft); font-weight:600; font-size:12px; letter-spacing:.04em; text-transform:uppercase; }
td.pass { color:var(--accent); font-weight:600; }
td.fail { color:var(--bad); font-weight:600; }
td.num { text-align:right; }
.tech details { border-top:1px solid var(--line); padding:12px 0; }
.tech summary { font-weight:600; }
summary em { color:var(--ink-soft); font-style:normal; font-weight:400; margin-left:8px; }
.step { display:grid; grid-template-columns:minmax(140px, 220px) minmax(80px, 1fr) 72px minmax(80px, 220px); gap:8px; align-items:center; margin:6px 0; font-size:13px; }
.step.sub .step-name { color:var(--ink-soft); }
.track { height:8px; background:var(--bg-soft); border-radius:99px; overflow:hidden; }
.fill { display:block; height:100%; background:var(--accent); border-radius:99px; }
.step.sub .fill { background:var(--accent-soft); }
.ms { text-align:right; font-variant-numeric:tabular-nums; }
.detail { color:var(--ink-soft); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.chart { width:100%; height:auto; margin:12px 0 16px; color:var(--ink); }
.chart rect { fill:var(--accent); }
.filters, .path-picks, .replay { display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin:8px 0 16px; }
.filters input, .filters select, .path-picks select { border-radius:12px; padding:8px 10px; }
.path-grid { display:flex; flex-direction:column; gap:28px; }
.path-pane { background:var(--bg); border-radius:16px; padding:16px 16px 12px; min-width:0; }
.path-summary { min-height:1.6em; color:var(--ink-soft); margin:0 0 8px; }
.zoom { display:flex; gap:8px; margin:0 0 10px; }
.zoom button, .replay button { padding:6px 12px; }
.flow-scroll { overflow:auto; max-width:100%; min-width:0; padding-bottom:8px; }
.flow-scale { transform-origin: top left; }
.flow { display:block; }
.col-title { fill:var(--ink); font-family:"Instrument Serif", Georgia, serif; font-size:18px; }
.human-band { fill:var(--bg-soft); stroke:var(--line); }
.edge { fill:none; }
.edge-flow { stroke:var(--accent); stroke-width:2.4; }
.edge-faint { stroke:var(--ink-faint); stroke-width:1; opacity:.55; }
.edge-dashed { stroke:var(--bad); stroke-width:1.6; stroke-dasharray:5 4; }
.edge-travel { stroke:var(--accent); stroke-width:3; }
.arrow-flow { fill:var(--accent); }
.arrow-faint { fill:var(--ink-faint); }
.arrow-dashed { fill:var(--bad); }
.loop-arrow { stroke:var(--warn); stroke-width:1.4; }
.join-shape { fill:var(--card); stroke:var(--accent); stroke-width:1.4; }
.diamond-label { fill:var(--ink); font:12px "Inter", system-ui, sans-serif; }
.travel-dot { fill:var(--accent); }
.node {
  width:100%; height:100%; box-sizing:border-box; text-align:left; position:relative;
  background:var(--card); color:var(--ink); border:1px solid var(--line); border-radius:16px;
  padding:8px 10px 8px 14px; cursor:pointer; font:12.5px/1.35 "Inter", system-ui, sans-serif;
}
.node .stripe { position:absolute; left:0; top:0; bottom:0; width:5px; border-radius:16px 0 0 16px; background:var(--accent); }
.node-label { display:block; font-weight:600; }
.node-meta, .node-state { display:block; color:var(--ink-soft); }
.node-state { font-size:11px; letter-spacing:.04em; text-transform:uppercase; color:var(--accent); }
.badge { display:inline-block; margin-top:4px; padding:1px 6px; border-radius:999px; background:var(--warn-bg); color:var(--warn); font-size:11px; }
.chip { display:inline-block; margin:4px 4px 0 0; padding:1px 6px; border-radius:999px; border:1px solid var(--line); background:var(--bg-soft); color:var(--ink); font-size:11px; }
.chip.is-fail { background:var(--bad-bg); color:var(--bad); }
.chip.is-retry { box-shadow:inset 0 0 0 1px var(--warn); }
.node.is-fail { background:var(--bad-bg); }
.node.is-fail .stripe { background:var(--bad); }
.node.is-fail .node-state { color:var(--bad); }
.node.is-retry .node-state { color:var(--warn); }
.node.is-injection { box-shadow:inset 0 0 0 1px var(--warn); }
.node.is-chosen { box-shadow:inset 0 0 0 1.5px var(--accent); }
.node.is-faded { background:transparent; border-style:dashed; }
.node.is-faded .stripe { background:var(--line); }
.node.is-faded .node-state { color:var(--ink-soft); }
.node.is-skipped { border-style:dashed; color:var(--ink-soft); }
.node.is-skipped .stripe { background:var(--line); }
.node.is-skipped .node-state { color:var(--ink-soft); }
.node.is-future, .chip.is-future { opacity:.38; }
.node.is-current { outline:2px solid var(--accent); outline-offset:2px; }
.chip.is-current { outline:1px solid var(--accent); }
.replay button[aria-pressed="true"] { background:var(--accent-soft); color:var(--ink); }
.scrub { display:flex; align-items:center; gap:8px; color:var(--ink-soft); flex:1; min-width:160px; }
.scrub input { flex:1; accent-color:var(--accent); }
.path-detail {
  margin:8px 0 0; white-space:pre-wrap; font:13px/1.5 ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace;
  background:var(--bg-soft); border-radius:12px; padding:12px; min-height:3em; color:var(--ink);
}
.log-scroll { max-height:420px; overflow:auto; }
code { font-size:.92em; }
:focus-visible { outline:2px solid var(--focus); outline-offset:3px; }
@media (max-width: 800px) {
  h1 { font-size:40px; }
  section { padding:20px; border-radius:16px; }
  .summary-grid { grid-template-columns:1fr; }
  .step { grid-template-columns:minmax(0, 1fr) 64px; }
  .detail { display:none; }
}
"""


def page_css() -> str:
    return _CSS
