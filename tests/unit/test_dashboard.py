"""Status page: export, loopback, redaction, unconfigured cost, and percentile rank."""

import json
import re
import runpy
import sys
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
from dispute_copilot.observability.cost import compute_cost, host_class, load_cost, note_model_span, price_call
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


def test_listed_prices_match_the_model_id_and_a_missing_model_is_named():
    table = load_cost()
    assert set(table["models"]) == {"claude-haiku-5-5", "clef-flash", "gpt-6-luna", "jev-latest"}
    assert price_call("gpt-6-luna", 1_000_000, 2_000_000, table) == {
        "status": "estimate from list price",
        "usd": 1.1,
    }
    assert price_call("gpt-6 luna", 1_000_000, 0, table)["usd"] == 0.1
    assert price_call("claude-haiku-5-5", 1_000_000, 2_000_000, table) == {
        "status": "estimate from list price",
        "usd": 1.1,
    }
    assert price_call("claude-haiku-5-5", 1000, 500, table) == {
        "status": "estimate from list price",
        "usd": 0.00035,
    }
    assert price_call("Claude-Haiku-5-5", 1_000_000, 0, table)["usd"] == 0.1
    assert price_call("anthropic.claude-haiku-5-5", 1_000_000, 0, table)["usd"] == 0.1
    assert price_call("claude-haiku-5-5-20261026", 1_000_000, 0, table)["usd"] == 0.1
    assert price_call("claude-haiku-5-5-2026-10-26", 1_000_000, 0, table)["usd"] == 0.1
    assert price_call("clef-flash", 1_000_000, 0, table)["usd"] == 0.038
    assert price_call("jev-latest", 1_000_000, 0, table)["usd"] == 0.042
    assert price_call("jev-latest", 1_000_000, 1_000_000, table)["usd"] == 0.042
    assert price_call("claude-haiku-4-5", 1_000_000, 0, table) == {
        "status": "no price configured for claude-haiku-4-5",
        "usd": None,
    }
    missing = price_call("other-model", 1000, 1000, table)
    assert missing == {"status": "no price configured for other-model", "usd": None}
    assert compute_cost() == {"status": "estimate, unmeasured", "usd": None}
    known = {"models": {"example-model": {"input_per_million": 2000, "output_per_million": 8000}}, "compute": {"usd": 1.5}}
    assert price_call("example-model", 1000, 1000, known) == {"status": "estimate from list price", "usd": 10.0}
    assert price_call("other-model", 1000, 1000, known)["status"] == "no price configured for other-model"
    assert compute_cost(known) == {"status": "provided", "usd": 1.5}
    records = [
        {
            "kind": "span",
            "name": "model.call",
            CASE_ID: "C",
            "run_id": "r",
            "duration_ms": 3,
            "attrs": {"model": "claude-haiku-5-5", "tokens_in": 1000, "tokens_out": 500, "where": "azure"},
        }
    ]
    view = build_view(records, [], cost_table=table)
    assert view["cost"]["calls"][0]["status"] == "estimate from list price"
    assert view["cost"]["calls"][0]["usd"] == 0.00035
    assert view["cost"]["run"]["status"] == "estimate from list price"
    assert view["cost"]["run"]["usd"] == 0.00035
    assert view["cost"]["compute"]["status"] == "estimate, unmeasured"
    page = render(view)
    assert "estimate from list price" in page
    assert "estimate, unmeasured" in page
    assert "Not configured." not in page


def test_priced_rollups_sum_only_configured_calls():
    table = {"models": {"example-model": {"input_per_million": 1000, "output_per_million": 1000}}, "compute": {}}
    records = [
        {
            "kind": "span",
            "name": "model.call",
            CASE_ID: "C",
            "run_id": "r",
            "duration_ms": 1,
            "attrs": {"model": "example-model", "tokens_in": 1000, "tokens_out": 1000, "where": "local"},
        }
    ]
    view = build_view(records, [], cost_table=table)
    assert view["cost"]["run"] == {"status": "estimate from list price", "usd": 2.0, "calls": 1}
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
    assert any(row["model"] == "not recorded" and row["cases"] == 12 for row in loaded["deployment"])
    assert all(row["where"] != "aws" for row in loaded["deployment"])
    assert "Other environments appear when their runs are loaded." in text
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


def test_cli_help_and_module_entrypoint(monkeypatch: pytest.MonkeyPatch):
    with pytest.raises(SystemExit) as caught:
        main(["--help"])
    assert caught.value.code == 0
    monkeypatch.setattr(sys, "argv", ["dispute_copilot", "--help"])
    with pytest.raises(SystemExit) as again:
        runpy.run_module("dispute_copilot", run_name="__main__")
    assert again.value.code == 0


def test_cli_help_rejects_a_bad_case_id():
    assert main(["run", "../CARD-D01"]) == 1


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
    monkeypatch.setenv("LLM_PROVIDER", "null")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with case_trace("C", "run-1", tmp_path, "run") as tracer:
        with tracer.span("case", outcome="drafted"):
            pass
    assert tracer.records[0]["label"] == "azure"
    assert tracer.records[0]["attrs"]["where"] == "azure"
    assert "model" not in tracer.records[0]["attrs"]
    text = json.dumps(tracer.records)
    assert "https://" not in text
    assert "http://" not in text
    view = build_view(tracer.records, [])
    azure = next(row for row in view["deployment"] if row["where"] == "azure")
    assert azure["model"] == "templates only (no model)"
    assert azure["measured"] is True
    assert all(row["where"] != "aws" for row in view["deployment"])


def test_critic_call_records_tokens_without_a_price(tmp_path: Path):
    class Critic:
        model = "example-model"

        def complete(self, *, system: str, user: str) -> Completion:
            del system, user
            body = json.dumps(
                {
                    "verdict": "block",
                    "reason": "Citations are present. No outcome is promised.",
                }
            )
            return Completion(body, 120, 15, self.model)

    with case_trace("C", "run-1", tmp_path, "agent-review") as tracer:
        _ask_critic({}, Critic())
    spans = [item for item in tracer.records if item.get("name") == "model.call"]
    assert len(spans) == 1
    assert spans[0]["attrs"]["tokens_in"] == 120
    assert spans[0]["attrs"]["tokens_out"] == 15
    assert spans[0]["attrs"]["cost_status"] == "no price configured for example-model"
    assert "cost_usd" not in spans[0]["attrs"]
    assert spans[0]["attrs"]["where"]
    dumped = json.dumps(spans)
    assert "https://" not in dumped
    assert "http://" not in dumped


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
    for link in re.findall(r"https?://[^\"'\s>]+", page):
        assert "127.0.0.1" in link
    assert "https://" not in page.lower()
    assert 'data-speed="0.5"' in page and "0.5x" in page
    assert "1x" in page and "2x" in page and "5x" in page
    assert "diamond-label" in page
    assert "if (!caption) return;" in page
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
    for link in re.findall(r"https?://[^\"'\s>]+", exported):
        assert "127.0.0.1" in link
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
    assert "<details id=\"technical-path\" open" not in exported
    assert 'id="client"' in exported
    assert 'id="card-latency"' in exported


def _diagram_node(path: dict, node_id: str) -> dict:
    return next(item for item in path["diagram"]["nodes"] if item["id"] == node_id)


def test_diagram_places_stages_branches_and_the_routing_fork():
    records = _trace("CARD-D07.jsonl") + _trace("CARD-D08.jsonl") + _trace("CARD-D10.jsonl")
    paths = build_paths(records, records)
    failed = next(item for item in paths if item["case"] == "CARD-D07")
    transactions = _diagram_node(failed, "transactions")
    merchant = _diagram_node(failed, "merchant")
    assert transactions["column"] == "evidence" and merchant["column"] == "evidence"
    assert transactions["slot"] != merchant["slot"]
    assert transactions["label"] == "Check transactions"
    assert len(transactions["chips"]) == 3
    assert [chip["code"] for chip in transactions["chips"]] == [503, 503, 503]
    assert transactions["chips"][0]["retry"] is False
    assert transactions["chips"][1]["retry"] is True and transactions["chips"][2]["retry"] is True
    assert transactions["loop"] is True
    assert "fail" in transactions["marks"]
    assert any(edge["kind"] == "dashed" and edge["from"] == "transactions" for edge in failed["diagram"]["edges"])
    assert _diagram_node(failed, "intake.parse")["column"] == "intake"
    assert _diagram_node(failed, "ledger.lookup")["column"] == "ledger"
    assert _diagram_node(failed, "rules.routing")["column"] == "rules"
    assert _diagram_node(failed, "draft.write")["column"] == "draft"
    assert _diagram_node(failed, "checklist")["column"] == "checklist"
    for name in ("review.submit", "approval", "record.precheck", "record.create"):
        node = _diagram_node(failed, name)
        assert node["column"] == "human"
        assert node["ran"] is False
        assert "not run" in node["marks"]
    outcomes = [node for node in failed["diagram"]["nodes"] if node["kind"] == "outcome"]
    assert len(outcomes) == 3
    chosen = [node for node in outcomes if node["chosen"]]
    assert len(chosen) == 1
    assert chosen[0]["faded"] is False
    assert all(node["faded"] for node in outcomes if not node["chosen"])
    assert any(edge["from"] == "rules.routing" and edge["to"] == chosen[0]["id"] and edge["kind"] == "flow" for edge in failed["diagram"]["edges"])
    flagged = next(item for item in paths if item["case"] == "CARD-D08")
    side = [node for node in flagged["diagram"]["nodes"] if node["kind"] == "side"]
    assert len(side) == 1
    assert side[0]["label"] == "Treated as data"
    assert any(edge["to"] == "treated-as-data" and edge["kind"] == "flow" for edge in flagged["diagram"]["edges"])
    retried = next(item for item in paths if item["case"] == "CARD-D10")
    again = _diagram_node(retried, "transactions")
    assert again["chips"][0]["fail"] is True and again["chips"][0]["retry"] is False
    assert again["chips"][1]["retry"] is True and again["chips"][1]["fail"] is False
    assert not any(edge["kind"] == "dashed" and edge["from"] == "transactions" for edge in retried["diagram"]["edges"])
    for path in paths:
        diamonds = [node for node in path["diagram"]["nodes"] if node["kind"] in {"fork", "join"}]
        assert diamonds
        for node in diamonds:
            assert node["label"].strip()
            assert node["tip"].strip()
    assert _diagram_node(failed, "fork-evidence")["label"] == "Parallel reads"
    assert _diagram_node(failed, "join-evidence")["label"] == "Join"


def test_unknown_step_keeps_a_fallback_column():
    records = [
        {
            "kind": "span",
            "name": "custom.step",
            CASE_ID: "C",
            "run_id": "r",
            "span_id": "s",
            "duration_ms": 2,
            "status": "ok",
            "attrs": {},
        }
    ]
    diagram = build_paths(records, [])[0]["diagram"]
    extra = next(node for node in diagram["nodes"] if node["name"] == "custom.step")
    assert extra["column"] == "other"
    assert extra["label"] == "Extra step"
    assert any(column["id"] == "other" for column in diagram["columns"])
    diamonds = [node for node in diagram["nodes"] if node["kind"] in {"fork", "join"}]
    assert {node["label"] for node in diamonds} == {"Parallel reads", "Join"}
    assert all(node["label"].strip() and node["tip"].strip() for node in diamonds)


def test_default_view_keeps_the_client_page_and_closes_the_rest():
    page = render(build_view(_trace("CARD-D01.jsonl"), [], cost_table={"models": {}, "compute": {}}))
    assert not re.search(r"<details\b[^>]*\sopen", page)
    head, technical = page.split('<details id="technical-path">', 1)
    assert 'id="client"' in head
    assert "Cases ran" in head
    assert "Ready for review" in head
    assert "Manual review" in head
    assert "More information" in head
    assert "Approach 1" in head
    assert 'id="status-line"' in head
    assert "fallback" not in head.lower()
    assert 'href="compare.html"' in head
    assert 'href="http://127.0.0.1:8080"' in head
    assert "fonts.googleapis" not in page
    assert "Instrument Serif" in page
    assert "Inter" in page
    for section_id in (
        "technical-path",
        "technical-latency",
        "technical-cost",
        "technical-logs",
        "technical-mcp",
        "technical-audit",
        "technical-overview",
    ):
        assert f'<details id="{section_id}">' in page
    assert "log-table" in technical
    assert "Timeline" in technical
    assert 'id="step-chart"' in technical
    assert "Good-case rate" in technical
    assert "Flagged injections" in technical


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
    svg = page.split('id="step-chart"', 1)[1].split("</svg>", 1)[0]
    widths = [float(item) for item in re.findall(r'width="([0-9.]+)"', svg)]
    assert widths
    assert min(widths) >= 60
    assert max(widths) > min(widths)


def _case_row(case: str, run: str, ms: float, where: str | None, label: str | None = None) -> dict:
    attrs: dict = {"outcome": "drafted"}
    if where is not None:
        attrs["where"] = where
    row = {"kind": "span", "name": "case", CASE_ID: case, "run_id": run, "duration_ms": ms, "attrs": attrs}
    if label is not None:
        row["label"] = label
    return row


def _model_row(case: str, run: str, where: str | None, model: str, tokens_in: int, tokens_out: int) -> dict:
    attrs: dict = {"model": model, "tokens_in": tokens_in, "tokens_out": tokens_out}
    if where is not None:
        attrs["where"] = where
    return {"kind": "span", "name": "model.call", CASE_ID: case, "run_id": run, "duration_ms": 1, "attrs": attrs}


def _where_row(view: dict, where: str, model: str) -> dict:
    return next(row for row in view["deployment"] if row["where"] == where and row["model"] == model)


def test_where_table_prices_an_azure_haiku_run():
    records = [
        _case_row("C", "r", 20, "azure", "azure"),
        _model_row("C", "r", "azure", "claude-haiku-5-5", 1_000_000, 0),
    ]
    view = build_view(records, [])
    row = _where_row(view, "azure", "claude-haiku-5-5")
    assert row["cases"] == 1
    assert row["passed"] == 1
    assert row["p50"] == 20
    assert row["p95"] == 20
    assert row["tokens_in"] == 1_000_000
    assert row["tokens_out"] == 0
    assert row["cost_usd"] == 0.1
    assert row["cost_status"] == "estimate from list price"
    assert all(item["where"] != "aws" for item in view["deployment"])
    page = render(view)
    assert "claude-haiku-5-5" in page
    assert "0.100000 estimate from list price" in page
    assert "No azure run loaded" not in page
    assert "<td>aws</td>" not in page
    card = page.split('id="card-cost"', 1)[1].split("</article>", 1)[0]
    assert "0.100000 estimate from list price" in card
    assert "Per case: C 0.100000 estimate from list price" in card


def test_where_table_shows_local_templates_without_empty_places():
    records = [_case_row("C1", "r", 10, "local"), _case_row("C2", "r", 30, "local")]
    view = build_view(records, [])
    assert len(view["deployment"]) == 1
    row = view["deployment"][0]
    assert row["where"] == "local"
    assert row["model"] == "templates only (no model)"
    assert row["cases"] == 2
    assert row["passed"] == 2
    assert row["tokens_in"] == 0
    assert row["tokens_out"] == 0
    assert row["cost_usd"] is None
    page = render(view)
    assert "templates only (no model)" in page
    assert "Other environments appear when their runs are loaded." in page
    assert "<td>azure</td>" not in page
    assert "<td>aws</td>" not in page
    card = page.split('id="card-cost"', 1)[1].split("</article>", 1)[0]
    assert "No model call was recorded." in card


def test_where_table_lists_each_saved_place_and_names_an_unpriced_model():
    mixed = [
        _case_row("A", "r", 20, "azure"),
        _model_row("A", "r", "azure", "claude-haiku-4-5", 1_000_000, 0),
        _case_row("B", "r", 15, "local"),
    ]
    view = build_view(mixed, [])
    assert {row["where"] for row in view["deployment"]} == {"azure", "local"}
    page = render(view)
    assert "Other environments appear when their runs are loaded." not in page
    assert "<td>aws</td>" not in page
    unpriced = [
        _case_row("C", "r", 12, "local"),
        _model_row("C", "r", "local", "mystery-model", 100, 50),
    ]
    missing = build_view(unpriced, [])
    row = _where_row(missing, "local", "mystery-model")
    assert row["cost_usd"] is None
    assert row["cost_status"] == "no price configured for mystery-model"
    assert row["tokens_in"] == 100
    assert row["tokens_out"] == 50
    text = render(missing)
    assert "no price configured for mystery-model" in text
    assert "Not configured." not in text
    legacy = [_case_row("C", "r", 15, None, "azure")]
    old = build_view(legacy, [])
    kept = old["deployment"]
    assert len(kept) == 1
    assert kept[0]["where"] == "azure"
    assert kept[0]["model"] == "not recorded"
    assert kept[0]["cost_usd"] is None


def test_place_word_comes_from_the_label_or_the_host_class(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    assert host_class("https://unit.services.ai.azure.com/v1") == "azure"
    assert host_class("http://127.0.0.1:9/v1") == "local"
    assert host_class("http://localhost/v1") == "local"
    assert host_class("http://[::1]/v1") == "local"
    assert host_class("https://example.invalid/v1") == "other"
    monkeypatch.setenv("LLM_PROVIDER", "null")
    monkeypatch.setenv("LLM_API_KEY", "super-secret-token")
    monkeypatch.setenv("RUN_LABEL", "local")
    monkeypatch.delenv("AZURE_BASE_URL", raising=False)
    monkeypatch.setenv("AZURE_BASE_URL", "https://unit.services.ai.azure.com/openai")
    with case_trace("C", "run-1", tmp_path) as tracer:
        with tracer.span("case"):
            pass
        with tracer.span("model.call") as span:
            note_model_span(span, Completion("ok", 10, 2, "claude-haiku-5-5"))
    text = json.dumps(tracer.records)
    assert "https://" not in text
    assert "http://" not in text
    assert "services.ai.azure.com" not in text
    assert "super-secret-token" not in text
    assert "authorization" not in text.lower()
    case = next(row for row in tracer.records if row["name"] == "case")
    call = next(row for row in tracer.records if row["name"] == "model.call")
    assert case["attrs"]["where"] == "local"
    assert "model" not in case["attrs"]
    assert call["attrs"]["where"] == "local"
    assert call["attrs"]["model"] == "claude-haiku-5-5"
    assert call["attrs"]["cost_status"] == "estimate from list price"
    monkeypatch.delenv("RUN_LABEL", raising=False)
    with case_trace("D", "run-2", tmp_path) as tracer:
        with tracer.span("case"):
            pass
    again = next(row for row in tracer.records if row["name"] == "case")
    assert again["attrs"]["where"] == "azure"
    assert "services.ai.azure.com" not in json.dumps(tracer.records)
