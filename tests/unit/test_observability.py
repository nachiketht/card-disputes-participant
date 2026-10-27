import json
import logging

from dispute_copilot.observability.logs import JsonFormatter, configure
from dispute_copilot.observability.metrics import compute, evaluate_alerts, load_slo, percentile
from dispute_copilot.observability.tracing import LOGGER, case_trace, current

SECRET = "SYNTHETIC-" + "PRIVATE-X"


def test_spans_nest_and_attributes_are_redacted(tmp_path):
    with case_trace("CARD-T1", "run-1", tmp_path) as tracer:
        with tracer.span("case") as root:
            with tracer.span("tool.call", dependency="merchant", note=SECRET):
                current().event("retry.backoff", attempt=1, text="x" * 500)
            root.set(outcome="drafted")
    lines = [json.loads(line) for line in (tmp_path / "CARD-T1.jsonl").read_text().splitlines()]
    assert [r["name"] for r in lines] == ["retry.backoff", "tool.call", "case"]
    event, call, root = lines
    assert call["attrs"]["note"] == "[redacted]"
    assert len(event["attrs"]["text"]) < 200
    assert event["parent_id"] == call["span_id"]
    assert call["parent_id"] == root["span_id"]
    assert {r["trace_id"] for r in lines} == {tracer.trace_id}
    assert SECRET not in (tmp_path / "CARD-T1.jsonl").read_text()


def test_no_tracer_is_a_no_op():
    with current().span("anything") as span:
        span.set(value=1)
    current().event("nothing")


def _case(ms, outcome="drafted", route="ready_for_review"):
    return {"kind": "span", "name": "case", "case_id": "C", "duration_ms": ms, "attrs": {"outcome": outcome, "route": route}}


def _call(outcome):
    return {"kind": "span", "name": "tool.call", "case_id": "C", "duration_ms": 1,
            "attrs": {"dependency": "transactions", "outcome": outcome, "attempts": 3 if outcome == "exhausted" else 1}}


def test_metrics_and_alerts_follow_the_slo():
    slo = load_slo()
    records = [_case(10), _case(20), _case(5000), _case(15, outcome="not_written")]
    records += [_call("ok"), _call("exhausted"), _call("exhausted"), _call("ok")]
    metrics = compute(records, slo)
    assert metrics["cases_total"] == 4
    assert metrics["valid_contract_rate"] == 0.75
    assert metrics["good_case_rate"] == 0.5
    assert metrics["tool_exhausted_rate"] == 0.5
    assert metrics["case_latency_ms"]["p95"] == 5000
    fired = {alert["alert"] for alert in evaluate_alerts(metrics, slo)}
    assert {"tool_exhausted_rate_high", "good_case_rate_below_slo"} <= fired


def test_a_good_case_is_a_draft_inside_the_latency_target_and_ignores_route():
    slo = load_slo()
    description = slo["slo"]["description"].casefold()
    assert slo["slo"]["latency_target_ms"] == 2000
    assert "draft" in description
    assert "not read" in description
    assert "escalation" not in description
    records = [
        _case(100, route="manual_review"),
        _case(2000, route="ready_for_review"),
        _case(2001, route="ready_for_review"),
        _case(10, outcome="not_written", route="manual_review"),
    ]
    metrics = compute(records, slo)
    assert metrics["cases_total"] == 4
    assert metrics["cases_drafted"] == 3
    assert metrics["good_case_rate"] == 0.5


def test_a_healthy_run_fires_no_page():
    metrics = compute([_case(10) for _ in range(20)] + [_call("ok") for _ in range(20)])
    assert [a for a in evaluate_alerts(metrics) if a["severity"] == "page"] == []


def test_percentile_nearest_rank():
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.95) == 10
    assert percentile([5], 0.95) == 5
    assert percentile([], 0.95) is None


def test_json_logs_mask_restricted_text_and_can_write_stderr(capsys, tmp_path):
    formatter = JsonFormatter()
    record = logging.LogRecord("dispute_copilot", logging.INFO, __file__, 1, "card %s", ("4111111111111111",), None)
    rendered = formatter.format(record)
    assert "4111111111111111" not in rendered
    try:
        configure(None, to_stderr=True)
        LOGGER.info("stderr-ping")
        captured = capsys.readouterr()
        assert "stderr-ping" in captured.err
    finally:
        configure(None)
