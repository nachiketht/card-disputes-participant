"""Status page: export, loopback, redaction, unconfigured cost, and percentile rank."""

import json
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from dispute_copilot.cli import main
from dispute_copilot.fields import CASE_ID, Routing
from dispute_copilot.llm.base import Completion
from dispute_copilot.observability.board import bind_page, build_view, load_view, render, write_html
from dispute_copilot.observability.path import build_paths
from dispute_copilot.observability.cost import compute_cost, load_cost, price_call
from dispute_copilot.observability.logs import LOGGER, configure
from dispute_copilot.observability.metrics import percentile
from dispute_copilot.observability.tracing import case_trace
from dispute_copilot.review_flow.agent_review import _ask_critic

ROOT = Path(__file__).resolve().parents[2]
DEV_REPORTS = ROOT / "evidence" / "week10" / "reports"
PAN = "4111111111111111"
TITLES = (
    "Run overview",
    "Request path",
    "Timeline",
    "Latency",
    "Cost",
    "Logs",
    "Security",
    "Approval",
    "Deployment",
)


def test_percentile_rank_matches_the_metrics_report():
    assert percentile([10, 20, 30, 40], 0.50) == 20
    assert percentile([10, 20, 30, 40], 0.95) == 40
    assert percentile(list(range(1, 101)), 0.95) == 95
    assert percentile(list(range(1, 21)), 0.95) == 19
    assert percentile([], 0.95) is None


def test_step_latency_uses_that_rank():
    records = [
        {"kind": "span", "name": "rules.classify", CASE_ID: "C", "duration_ms": ms, "run_id": "r", "attrs": {}}
        for ms in (10, 20, 30, 40)
    ]
    view = build_view(records, [], cost_table={"models": {}, "compute": {}})
    step = next(item for item in view["latency"]["by_step"] if item["name"] == "rules.classify")
    assert step["p50"] == 20
    assert step["p95"] == 40
    assert step["max"] == 40
    assert step["n"] == 4


def test_missing_price_is_not_configured_and_not_a_number():
    table = load_cost()
    assert table["models"] == {}
    priced = price_call("claude-haiku-4-5", 1000, 500)
    assert priced == {"status": "not configured", "usd": None}
    assert compute_cost() == {"status": "estimate, unmeasured", "usd": None}
    known = {"models": {"example-model": {"input_per_1k": 2, "output_per_1k": 8}}, "compute": {"usd": 1.5}}
    assert price_call("example-model", 1000, 1000, known) == {"status": "estimate", "usd": 10.0}
    assert price_call("other-model", 1000, 1000, known)["status"] == "not configured"
    assert compute_cost(known) == {"status": "provided", "usd": 1.5}
    records = [
        {
            "kind": "span",
            "name": "model.call",
            CASE_ID: "C",
            "run_id": "r",
            "duration_ms": 3,
            "attrs": {"model": "claude-haiku-4-5", "tokens_in": 1000, "tokens_out": 500},
        }
    ]
    view = build_view(records, [], cost_table=table)
    assert view["cost"]["calls"][0]["status"] == "not configured"
    assert view["cost"]["calls"][0]["usd"] is None
    assert view["cost"]["run"]["status"] == "not configured"
    assert view["cost"]["run"]["usd"] is None
    assert view["cost"]["compute"]["status"] == "estimate, unmeasured"
    page = render(view)
    assert "not configured" in page
    assert "estimate, unmeasured" in page


def test_priced_rollups_sum_only_configured_calls():
    table = {"models": {"example-model": {"input_per_1k": 1, "output_per_1k": 1}}, "compute": {}}
    records = [
        {
            "kind": "span",
            "name": "model.call",
            CASE_ID: "C",
            "run_id": "r",
            "duration_ms": 1,
            "attrs": {"model": "example-model", "tokens_in": 1000, "tokens_out": 1000},
        }
    ]
    view = build_view(records, [], cost_table=table)
    assert view["cost"]["run"] == {"status": "estimate", "usd": 2.0, "calls": 1}
    assert view["cost"]["by_case"][0]["usd"] == 2.0


def test_export_from_the_dev_run_lists_the_panels(tmp_path: Path):
    dest = tmp_path / "out.html"
    write_html(DEV_REPORTS, dest)
    text = dest.read_text(encoding="utf-8")
    for title in TITLES:
        assert title in text
    loaded = load_view(DEV_REPORTS)
    assert loaded["overview"]["counts"]["cases"] == 12
    assert "CARD-D01" in text
    assert "estimate, unmeasured" in text
    assert any(row["label"] == "aws" and row["status"] == "unmeasured" for row in loaded["deployment"])
    assert PAN not in text


def test_rendered_page_masks_a_card_number(tmp_path: Path):
    reports = tmp_path / "reports"
    traces = reports / "traces"
    traces.mkdir(parents=True)
    event = {
        "kind": "event",
        "name": "injection_detected",
        CASE_ID: "C1",
        "run_id": "r1",
        "level": "warning",
        "attrs": {"note": f"pay {PAN} now"},
    }
    (traces / "C1.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
    logs = reports / "logs"
    logs.mkdir()
    (logs / "run.jsonl").write_text(
        json.dumps({"name": "log", "message": PAN, CASE_ID: "C1", "level": "warning", "run_id": "r1"}) + "\n",
        encoding="utf-8",
    )
    dest = tmp_path / "out.html"
    write_html(reports, dest)
    text = dest.read_text(encoding="utf-8")
    assert PAN not in text
    assert "[redacted]" in text


def test_server_binds_loopback_and_refuses_writes():
    server = bind_page("<p>status ok</p>", 0)
    host, port = server.server_address[:2]
    assert host == "127.0.0.1"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as response:
            assert response.status == 200
            assert b"status ok" in response.read()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
            body = json.loads(response.read().decode("utf-8"))
            assert body == {"status": "ok", "read_only": True}
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/",
                data=b"{}" if method == "POST" else None,
                method=method,
            )
            with pytest.raises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request, timeout=2)
            assert caught.value.code == 405
            assert b"read-only" in caught.value.read()
    finally:
        server.shutdown()
        server.server_close()


def test_cli_export_and_rejected_host_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))
    dest = tmp_path / "nested" / "out.html"
    assert main(["dashboard", "--export", str(dest)]) == 0
    text = dest.read_text(encoding="utf-8")
    for title in TITLES:
        assert title in text
    with pytest.raises(SystemExit) as caught:
        main(["dashboard", "--host", "192.0.2.1", "--export", str(tmp_path / "no.html")])
    assert caught.value.code == 2


def test_run_label_is_stored_on_the_trace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RUN_LABEL", "azure")
    with case_trace("C", "run-1", tmp_path, "run") as tracer:
        with tracer.span("case", outcome="drafted"):
            pass
    assert tracer.records[0]["label"] == "azure"
    view = build_view(tracer.records, [], cost_table={"models": {}, "compute": {}})
    azure = next(row for row in view["deployment"] if row["label"] == "azure")
    aws = next(row for row in view["deployment"] if row["label"] == "aws")
    assert azure["measured"] is True
    assert aws["status"] == "unmeasured"
    assert aws["p95"] is None


def test_critic_call_records_tokens_without_a_price(tmp_path: Path):
    class Critic:
        model = "example-model"

        def complete(self, *, system: str, user: str) -> Completion:
            del system, user
            return Completion("block", 120, 15, self.model)

    with case_trace("C", "run-1", tmp_path, "agent-review") as tracer:
        _ask_critic({}, Critic())
    spans = [item for item in tracer.records if item.get("name") == "model.call"]
    assert len(spans) == 1
    assert spans[0]["attrs"]["tokens_in"] == 120
    assert spans[0]["attrs"]["tokens_out"] == 15
    assert spans[0]["attrs"]["cost_status"] == "not configured"
    assert "cost_usd" not in spans[0]["attrs"]


def test_stdout_log_option(capsys: pytest.CaptureFixture[str], tmp_path: Path):
    try:
        configure(tmp_path / "run.jsonl", to_stdout=True)
        LOGGER.info("ping", extra={"record": {"name": "ping", "run_id": "r"}})
        captured = capsys.readouterr()
        assert "ping" in captured.out
        assert "ping" in (tmp_path / "run.jsonl").read_text(encoding="utf-8")
    finally:
        configure(None)


def test_audit_file_fills_human_review_state(tmp_path: Path):
    reports = tmp_path / "reports"
    traces = reports / "traces"
    traces.mkdir(parents=True)
    (traces / "C.jsonl").write_text(
        json.dumps(
            {
                "kind": "event",
                "name": "review.agent_checklist",
                CASE_ID: "C",
                "run_id": "r",
                "attrs": {"passed": True},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (reports / "audit.json").write_text(
        json.dumps([{"event": "human_approved", CASE_ID: "C", "at": "t"}]),
        encoding="utf-8",
    )
    view = load_view(reports)
    row = next(item for item in view["approval"] if item["case"] == "C")
    assert row["checklist"] == "passed"
    assert row["human"] == "approved"
    assert row["record"] == "not created"


def _trace(name: str) -> list[dict]:
    path = DEV_REPORTS / "traces" / name
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _attempts(path: dict, dependency: str) -> list[dict]:
    return [
        node
        for node in path["nodes"]
        if node["name"].endswith(".attempt") and node["attrs"].get("dependency") == dependency
    ]


def test_path_marks_retries_failures_injection_and_skipped_steps():
    records = _trace("CARD-D07.jsonl") + _trace("CARD-D08.jsonl") + _trace("CARD-D10.jsonl")
    paths = build_paths(records, records)
    failed = next(item for item in paths if item["case"] == "CARD-D07")
    attempts = _attempts(failed, "transactions")
    assert [node["attrs"]["attempt"] for node in attempts] == [1, 2, 3]
    assert all(node["attrs"]["http_status"] == 503 for node in attempts)
    assert all("fail" in node["marks"] for node in attempts)
    assert "retry" not in attempts[0]["marks"]
    assert "retry" in attempts[1]["marks"] and "retry" in attempts[2]["marks"]
    assert failed["ended"] == Routing.MANUAL_REVIEW.value.replace("_", " ")
    skipped = next(node for node in failed["nodes"] if node["name"] == "review.submit")
    assert skipped["ran"] is False
    assert skipped["duration_ms"] is None
    assert skipped["marks"] == ["not run"]
    for name in ("record.precheck", "record.create"):
        node = next(item for item in failed["nodes"] if item["name"] == name)
        assert node["duration_ms"] is None
        assert "not run" in node["marks"]
    flagged = next(item for item in paths if item["case"] == "CARD-D08")
    assert flagged["injection"] is True
    assert any("injection" in node["marks"] for node in flagged["nodes"])
    assert flagged["ended"] == Routing.READY_FOR_REVIEW.value.replace("_", " ")
    retried = next(item for item in paths if item["case"] == "CARD-D10")
    again = _attempts(retried, "transactions")
    assert "fail" in again[0]["marks"] and "retry" not in again[0]["marks"]
    assert "retry" in again[1]["marks"] and "fail" not in again[1]["marks"]
    assert again[1]["attrs"]["http_status"] == 200


def test_path_escapes_untrusted_text_and_replay_stays_offline(tmp_path: Path):
    hostile = "<script>alert(1)</script>"
    records = [
        {
            "kind": "span",
            "name": "intake.parse",
            CASE_ID: "C",
            "run_id": "r",
            "span_id": "s1",
            "duration_ms": 1.5,
            "status": "ok",
            "attrs": {"source": hostile},
        }
    ]
    logs = [
        {
            "kind": "event",
            "name": "note",
            CASE_ID: "C",
            "run_id": "r",
            "parent_id": "s1",
            "level": "warning",
            "message": hostile,
        }
    ]
    view = build_view(records, logs, cost_table={"models": {}, "compute": {}})
    page = render(view)
    assert hostile not in page
    assert "\\u003cscript\\u003e" in page
    assert "fetch(" not in page.lower()
    assert "xmlhttprequest" not in page.lower()
    assert "websocket" not in page.lower()
    assert "http://" not in page.lower()
    assert "https://" not in page.lower()
    assert "1x" in page and "2x" in page and "5x" in page
    assert "Pause" in page and "Restart" in page
    assert 'id="path-scrub-a"' in page and 'id="path-case-b"' in page
    raw = page.split('id="path-data">', 1)[1].split("</script>", 1)[0]
    loaded = json.loads(raw)
    node = loaded[0]["nodes"][0]
    assert node["attrs"]["source"] == hostile
    assert node["logs"][0]["text"] == hostile
    skipped = next(item for item in loaded[0]["nodes"] if item["name"] == "review.submit")
    assert skipped["duration_ms"] is None
    dest = tmp_path / "out.html"
    write_html(DEV_REPORTS, dest)
    exported = dest.read_text(encoding="utf-8")
    lowered = exported.lower()
    assert "http://" not in lowered
    assert "https://" not in lowered
    assert "ws://" not in lowered
    assert "fetch(" not in lowered
    assert "@import" not in lowered
    assert "fonts.googleapis" not in lowered
    assert "Instrument Serif" in exported
    assert "prefers-color-scheme: dark" in exported
    assert "#2f5f63" in exported and "#7eb0b4" in exported
    assert 'id="theme-toggle"' in exported
    assert "<th></th>" not in exported
    assert exported.count("<th>Status</th>") >= 3
    assert 'id="log-count"' in exported


def test_latency_chart_keeps_short_steps_visible():
    records = [
        {
            "kind": "span",
            "name": "case",
            CASE_ID: "C",
            "run_id": "r",
            "duration_ms": 151.371,
            "attrs": {"outcome": "drafted"},
        },
        {
            "kind": "span",
            "name": "intake.parse",
            CASE_ID: "C",
            "run_id": "r",
            "duration_ms": 0.046,
            "attrs": {},
        },
    ]
    page = render(build_view(records, [], cost_table={"models": {}, "compute": {}}))
    svg = page.split('<svg class="chart"', 1)[1].split("</svg>", 1)[0]
    widths = [float(item) for item in re.findall(r'width="([0-9.]+)"', svg)]
    assert widths
    assert min(widths) >= 60
    assert max(widths) > min(widths)
