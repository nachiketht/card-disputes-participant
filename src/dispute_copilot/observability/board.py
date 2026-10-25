"""Read-only status page built from traces, JSON logs, and the cost file.

The page is a single HTML file. The server binds 127.0.0.1 and answers GET
only. It does not approve, reject, reset, or post.
"""

from __future__ import annotations

import json
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dispute_copilot.evidence.redaction import Redactor
from dispute_copilot.fields import CASE_ID
from dispute_copilot.observability.cost import compute_cost, load_cost, price_call
from dispute_copilot.observability.metrics import compute, evaluate_alerts, load_slo, percentile, read_traces

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
COMPARE_LABELS = ("local", "azure", "aws")
_LOG_LIMIT = 400
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
    view = {
        "target_ms": target,
        "overview": _overview(spans, metrics, evaluate_alerts(metrics, rules), target),
        "waterfall": _waterfall(spans),
        "latency": _latency(spans, target),
        "cost": _cost(spans, table),
        "logs": _logs(log_rows, records),
        "security": _security(events, log_rows),
        "approval": _approval(records, audit),
        "deployment": _deployment(spans, table),
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


def write_html(reports_dir: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render(load_view(reports_dir)), encoding="utf-8")


def bind_page(html: str, port: int) -> ThreadingHTTPServer:
    handler = _handler(html.encode("utf-8"))
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


def _deployment(spans: list[dict], table: dict) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for span in spans:
        if span.get("name") != "case":
            continue
        grouped.setdefault(_label(span), []).append(span)
    labels = list(COMPARE_LABELS)
    for label in sorted(grouped):
        if label not in labels:
            labels.append(label)
    rows = []
    for label in labels:
        items = grouped.get(label) or []
        durations = [value for value in (_ms(item.get("duration_ms")) for item in items) if value is not None]
        calls = []
        # Cost for the label is filled by the caller via case ids below.
        case_ids = {_case(item) for item in items}
        rows.append(
            {
                "label": label,
                "cases": len(items),
                "p50": None if not durations else round(percentile(durations, 0.50) or 0, 3),
                "p95": None if not durations else round(percentile(durations, 0.95) or 0, 3),
                "max": None if not durations else round(max(durations), 3),
                "measured": bool(items),
                "status": "measured" if items else "unmeasured",
                "case_ids": sorted(case_ids),
            }
        )
    priced = _cost(spans, table)
    by_case = {item["case"]: item for item in priced["by_case"]}
    for row in rows:
        if not row["measured"]:
            row["cost_status"] = "unmeasured"
            row["cost_usd"] = None
            continue
        selected = [by_case[case] for case in row["case_ids"] if case in by_case]
        if not selected:
            row["cost_status"] = "unmeasured"
            row["cost_usd"] = None
            continue
        rolled = _rollup(selected)
        row["cost_status"] = rolled["status"]
        row["cost_usd"] = rolled["usd"]
    for row in rows:
        row.pop("case_ids", None)
    return rows


def _page(view: dict) -> str:
    overview = view["overview"]
    counts = overview["counts"]
    slo = overview["slo"]
    parts = [
        "<!DOCTYPE html>",
        "<html lang=\"en\"><head><meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        "<title>Run status</title>",
        f"<style>{_CSS}</style></head><body>",
        "<header><p class=\"mark\">Read-only</p><h1>Run status</h1>",
        "<p class=\"lede\">Traces, logs, latency, and cost from the files already written. "
        "This page does not approve, reject, reset, or post. "
        "A served copy listens on 127.0.0.1 only.</p>",
        "<nav>",
        "<a href=\"#overview\">Overview</a>",
        "<a href=\"#timeline\">Timeline</a>",
        "<a href=\"#latency\">Latency</a>",
        "<a href=\"#cost\">Cost</a>",
        "<a href=\"#logs\">Logs</a>",
        "<a href=\"#security\">Security</a>",
        "<a href=\"#approval\">Approval</a>",
        "<a href=\"#deployment\">Deployment</a>",
        "</nav></header><main>",
        _section(
            "1",
            "Run overview",
            "overview",
            _stat_row(counts, slo) + _alert_list(overview["alerts"]) + _overview_table(overview["rows"]),
        ),
        _section("2", "Timeline", "timeline", _timeline(view["waterfall"])),
        _section("3", "Latency", "latency", _latency_block(view["latency"])),
        _section("4", "Cost", "cost", _cost_block(view["cost"])),
        _section("5", "Logs", "logs", _log_block(view["logs"])),
        _section("6", "Security", "security", _security_block(view["security"])),
        _section("7", "Approval", "approval", _approval_block(view["approval"])),
        _section("8", "Deployment", "deployment", _deployment_block(view["deployment"], view["cost"]["compute"])),
        "</main><footer><p>Token prices are estimates from the cost file. "
        "A model with no price is not configured. "
        "Container time is estimate, unmeasured unless a measured usd figure is in that file. "
        "Values pass through the same redaction as the logs.</p></footer>",
        _FILTER_SCRIPT,
        "</body></html>",
    ]
    return "".join(parts)


def _section(number: str, title: str, anchor: str, body: str) -> str:
    return (
        f"<section id=\"{anchor}\"><h2><span class=\"num\">{number}</span> {escape(title)}</h2>{body}</section>"
    )


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
    )
    head = (
        f"<p class=\"note\">p50, p95, and max use the nearest rank above the fraction "
        f"(the same rank as the metrics report). SLO target {_num(latency['target_ms'])} ms. "
        "A step with no samples is unmeasured. The chart scale follows the measured p95 values, "
        "so a 2000 ms target is named here rather than drawn over short steps.</p>"
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
        f"<th>max</th><th></th></tr></thead><tbody>{''.join(body)}</tbody></table>"
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
        "<h3>Per case</h3><table><thead><tr><th>Case</th><th>ms</th><th>Target</th><th></th></tr></thead>"
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
        "<p class=\"note\">Per-call figures use the price table. "
        "Not configured means the model has no price. "
        "Unmeasured means the tokens were not recorded. "
        "A roll-up is a number only when every included call has an estimate.</p>"
    )
    chart_items = [(item["model"], item["usd"]) for item in cost["calls"] if item["status"] == "estimate"]
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
    note = f"<p class=\"note\">{logs['total']} lines"
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
        "<p class=\"note\">Rows come from the run label (<code>--label</code> or <code>RUN_LABEL</code>). "
        "local, azure, and aws stay on the page. A label with no traces is unmeasured. "
        f"Compute for this file: {escape(_money(compute))}.</p>"
    )
    chart = _svg_bars(
        [(item["label"], item["p95"]) for item in rows if item["measured"]],
        target=None,
        unit="ms",
    )
    body = []
    for item in rows:
        body.append(
            "<tr>"
            f"<td>{escape(item['label'])}</td>"
            f"<td class=\"num\">{item['cases']}</td>"
            f"<td class=\"num\">{escape(_num(item['p50']))}</td>"
            f"<td class=\"num\">{escape(_num(item['p95']))}</td>"
            f"<td class=\"num\">{escape(_num(item['max']))}</td>"
            f"<td>{escape(item['cost_status'] if item['cost_usd'] is None else _money({'status': item['cost_status'], 'usd': item['cost_usd']}))}</td>"
            f"<td>{escape(item['status'])}</td>"
            "</tr>"
        )
    table = (
        "<table><thead><tr><th>Label</th><th>Cases</th><th>p50</th><th>p95</th><th>max</th>"
        f"<th>Model cost</th><th></th></tr></thead><tbody>{''.join(body)}</tbody></table>"
    )
    return note + (chart or "<p class=\"empty\">No labeled runs to chart.</p>") + table


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


def _svg_bars(items: list[tuple[str, float | None]], *, target: float | None, unit: str) -> str:
    measured = [(name, value) for name, value in items if isinstance(value, (int, float)) and not isinstance(value, bool)]
    if not measured:
        return ""
    peak = max(value for _, value in measured) or 1
    if target and target <= peak * 3:
        peak = max(peak, target)
    row_h = 22
    label_w = 168
    chart_w = 360
    height = row_h * len(items) + 10
    width = label_w + chart_w + 92
    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(unit)}">',
    ]
    for index, (name, value) in enumerate(items):
        y = 6 + index * row_h
        parts.append(
            f'<text x="0" y="{y + 12}" fill="#3f3a33" font-size="11">{escape(name)}</text>'
        )
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            parts.append(f'<text x="{label_w}" y="{y + 12}" fill="#7a7368" font-size="11">unmeasured</text>')
            continue
        bar = chart_w * (float(value) / peak)
        parts.append(
            f'<rect x="{label_w}" y="{y}" width="{bar:.2f}" height="14" rx="2" fill="#0f6e56"></rect>'
        )
        parts.append(
            f'<text x="{label_w + bar + 6:.1f}" y="{y + 12}" fill="#3f3a33" font-size="11">{value:.3f}</text>'
        )
    if target and target <= peak * 3:
        x = label_w + chart_w * (target / peak)
        parts.append(f'<line x1="{x:.2f}" y1="0" x2="{x:.2f}" y2="{height}" stroke="#8a4b08" stroke-dasharray="3 3"></line>')
    parts.append("</svg>")
    return "".join(parts)


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


def _handler(page: bytes):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in {"/", "/index.html"}:
                self._send(200, "text/html; charset=utf-8", page)
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
    statuses = {item["status"] for item in calls}
    if statuses == {"estimate"}:
        total = round(sum(float(item["usd"] or 0) for item in calls), 6)
        return {"status": "estimate", "usd": total, "calls": len(calls)}
    if "not configured" in statuses:
        return {"status": "not configured", "usd": None, "calls": len(calls)}
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
    if item.get("status") == "estimate" and isinstance(item.get("usd"), (int, float)):
        return f"{item['usd']:.6f}"
    if item.get("status") == "provided" and isinstance(item.get("usd"), (int, float)):
        return f"{item['usd']:.6f}"
    return str(item.get("status") or "unmeasured")


def _num(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value)


_CSS = """
:root { color-scheme: light; --ink:#1c1915; --muted:#5e584e; --line:#e4dccb; --paper:#f6f1e7; --card:#fffdf8; --good:#0f6e56; --bad:#8d1d1d; --warn:#8a4b08; }
* { box-sizing: border-box; }
body { margin:0; background:var(--paper); color:var(--ink); font:16px/1.45 "Segoe UI", "Helvetica Neue", sans-serif; }
header, main, footer { width:min(1120px, calc(100% - 32px)); margin:0 auto; }
header { padding:28px 0 8px; }
.mark { letter-spacing:.14em; text-transform:uppercase; font-size:12px; color:var(--good); margin:0 0 6px; }
h1 { font: 600 40px/1.1 Georgia, "Iowan Old Style", serif; margin:0; }
.lede { max-width:62ch; color:var(--muted); }
nav { display:flex; flex-wrap:wrap; gap:8px 14px; padding:8px 0 18px; }
nav a { color:var(--ink); text-decoration:none; border-bottom:1px solid var(--line); }
main { display:grid; gap:18px; padding-bottom:28px; }
section { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:16px 18px 18px; }
h2 { font: 600 22px/1.2 Georgia, serif; margin:0 0 12px; }
h3 { font-size:15px; margin:16px 0 8px; }
.num { color:var(--good); margin-right:8px; }
.stats { display:grid; grid-template-columns:repeat(auto-fit, minmax(120px, 1fr)); gap:10px; }
.stats div { border:1px solid var(--line); border-radius:10px; padding:8px 10px; }
.stats p { margin:0; color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.04em; }
.stats strong { font-size:22px; font-variant-numeric:tabular-nums; }
.note, .empty { color:var(--muted); }
.ok { color:var(--good); }
.alerts { margin:8px 0; padding-left:18px; color:var(--warn); }
table { width:100%; border-collapse:collapse; font-size:14px; font-variant-numeric:tabular-nums; }
th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
th { color:var(--muted); font-weight:600; font-size:12px; letter-spacing:.03em; text-transform:uppercase; }
td.pass { color:var(--good); font-weight:600; }
td.fail { color:var(--bad); font-weight:600; }
td.num { text-align:right; }
details { border-top:1px solid var(--line); padding:8px 0; }
summary { font-weight:600; }
summary em { color:var(--muted); font-style:normal; font-weight:400; margin-left:8px; }
.step { display:grid; grid-template-columns:minmax(140px, 220px) minmax(80px, 1fr) 72px minmax(80px, 220px); gap:8px; align-items:center; margin:4px 0; font-size:13px; }
.step.sub .step-name { color:var(--muted); }
.track { height:10px; background:#efe8da; border-radius:99px; overflow:hidden; }
.fill { display:block; height:100%; background:var(--good); border-radius:99px; }
.step.sub .fill { background:#8aa89a; }
.ms { text-align:right; font-variant-numeric:tabular-nums; }
.detail { color:var(--muted); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.chart { width:100%; height:auto; margin:8px 0 12px; }
.filters { display:flex; flex-wrap:wrap; gap:8px; margin:8px 0; }
.filters input, .filters select { border:1px solid var(--line); background:#fff; border-radius:8px; padding:6px 8px; font:inherit; }
.log-scroll { max-height:420px; overflow:auto; }
footer { color:var(--muted); font-size:13px; padding-bottom:32px; }
code { font-size:.92em; }
@media (max-width: 720px) {
  h1 { font-size: 32px; }
  .step { grid-template-columns: minmax(0, 1fr) 64px; }
  .detail { display: none; }
  section { padding: 12px; }
}
"""

_FILTER_SCRIPT = """
<script>
(function () {
  var ids = ["f-run", "f-case", "f-span", "f-event", "f-level"];
  function apply() {
    var run = document.getElementById("f-run").value.trim().toLowerCase();
    var caseId = document.getElementById("f-case").value.trim().toLowerCase();
    var span = document.getElementById("f-span").value.trim().toLowerCase();
    var eventName = document.getElementById("f-event").value.trim().toLowerCase();
    var level = document.getElementById("f-level").value.trim().toLowerCase();
    var rows = document.querySelectorAll("#log-table tbody tr");
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      var show = (!run || (row.dataset.run || "").toLowerCase().indexOf(run) !== -1)
        && (!caseId || (row.dataset.case || "").toLowerCase().indexOf(caseId) !== -1)
        && (!span || (row.dataset.span || "").toLowerCase().indexOf(span) !== -1)
        && (!eventName || (row.dataset.event || "").toLowerCase().indexOf(eventName) !== -1)
        && (!level || (row.dataset.level || "").toLowerCase() === level);
      row.hidden = !show;
    }
  }
  for (var j = 0; j < ids.length; j++) {
    document.getElementById(ids[j]).addEventListener("input", apply);
    document.getElementById(ids[j]).addEventListener("change", apply);
  }
})();
</script>
"""
