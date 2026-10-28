"""CLI command branches, env parsing, and the run-all skip path."""

import json
import os
from argparse import Namespace
from pathlib import Path

import pytest

from dispute_copilot.adapters.case_system import FakeCaseSystem
from dispute_copilot.adapters.corpus import FileCorpus
from dispute_copilot.adapters.tools import FakeToolClient
from dispute_copilot.cli import (
    _compare,
    _env_line,
    _expand_env,
    _load_env_file,
    _review_command,
    _run_all,
    _run_one,
    _safe_case,
    _serve_review_page,
    _summary,
    _traced_case,
    main,
    run_all_report,
)
from dispute_copilot.config import Settings
from dispute_copilot.errors import CopilotError, CreateRejected, OutsideCheckpoint
from dispute_copilot.fields import CASE_ID, CLASSIFICATION, DEADLINE, FACTS, POLICY_IDS, ROUTING
from dispute_copilot.storage import write_draft

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "dev"


def _draft() -> dict:
    return {
        CASE_ID: "CARD-D01",
        CLASSIFICATION: "unauthorized",
        ROUTING: "ready_for_review",
        POLICY_IDS: ["CARD-POLICY-V2", "CARD-PROCEDURE-01"],
        DEADLINE: None,
        FACTS: {"transaction_id": "TX-D01"},
    }


def _settings(tmp_path: Path) -> Settings:
    return Settings("http://127.0.0.1:9", DATA, tmp_path / "submissions", tmp_path / "reports")


def _tools(case_id: str = "CARD-D01") -> FakeToolClient:
    payload = {
        "source_id": f"{case_id}-TRANSACTIONS",
        "transactions": [
            {
                "transaction_id": "TX-D01",
                "amount": 117,
                "currency": "XCR",
                "posted_date": "2026-08-19",
                "merchant_id": "MER-MORROW",
            }
        ],
    }
    merchant = {"source_id": "MERCHANT", "display_name": "Morrow Supplies", "note": "ok"}
    case = {"case_id": case_id, "intake_source": f"{case_id}-INTAKE", "documents": [f"{case_id}-INTAKE", f"{case_id}-ATTACHMENT"]}
    return FakeToolClient(cases=[case], transactions={case_id: [(200, payload)]}, merchants={case_id: [(200, merchant)]})


def test_a_path_case_id_is_rejected():
    assert _safe_case(None) is True
    assert _safe_case("CARD-D01") is True
    assert _safe_case("") is False
    assert _safe_case(".") is False
    assert _safe_case("..") is False
    assert _safe_case("a/b") is False
    assert _safe_case("a\\b") is False


def test_env_lines_skip_comments_and_expand_references():
    assert _env_line("# comment", {}) is None
    assert _env_line("export NAME=value", {}) == ("NAME", "value")
    assert _env_line("no-equals", {}) is None
    assert _env_line("1BAD=x", {}) is None
    assert _env_line("KEY='${OTHER}'", {"OTHER": "nope"}) == ("KEY", "${OTHER}")
    assert _env_line('KEY="quoted # still"', {}) == ("KEY", "quoted # still")
    assert _env_line("KEY=plain # trailing", {}) == ("KEY", "plain")
    assert _expand_env("$MISSING", "", {}) == ""
    found = {"HOST": "example"}
    assert _expand_env("https://${HOST}/v1", "", found) == "https://example/v1"
    assert _expand_env("https://$HOST/v1", "", found) == "https://example/v1"


def test_load_env_file_fills_empty_keys_and_skips_a_duplicate_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    env_file = tmp_path / ".env"
    env_file.write_text("FROM_FILE=one\nALREADY=file\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALREADY", "process")
    monkeypatch.delenv("FROM_FILE", raising=False)
    _load_env_file()
    assert os.environ["FROM_FILE"] == "one"
    assert os.environ["ALREADY"] == "process"
    env_file.write_text("FROM_FILE=two\n", encoding="utf-8")
    _load_env_file()
    assert os.environ["FROM_FILE"] == "one"


def test_metrics_prints_a_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "null")
    assert main(["metrics", "--label", "azure-run"]) == 0
    assert os.environ["RUN_LABEL"] == "azure-run"
    payload = json.loads(capsys.readouterr().out)
    assert "metrics" in payload
    assert "alerts_fired" in payload
    assert (tmp_path / "metrics.json").is_file()


def test_evals_uses_the_scorecard_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))
    monkeypatch.setenv("SUBMISSIONS_DIR", str(tmp_path / "submissions"))

    def fake_write(**kwargs):
        assert kwargs["report_path"] == tmp_path / "evals.json"
        return 1, "scorecard\nfailed"

    monkeypatch.setattr("dispute_copilot.evals.scorecard.write_scorecard", fake_write)
    assert main(["evals", "--report", str(tmp_path / "evals.json")]) == 1
    assert capsys.readouterr().out.strip() == "scorecard\nfailed"


def test_dashboard_rejects_a_port_outside_the_range(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))
    assert main(["dashboard", "--port", "0"]) == 1
    assert "port must be from 1 to 65535" in capsys.readouterr().err


def test_dashboard_closes_after_an_interrupt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))
    closed = {"done": False}

    class FakeServer:
        server_address = ("127.0.0.1", 8754)

        def serve_forever(self):
            raise KeyboardInterrupt()

        def server_close(self):
            closed["done"] = True

    monkeypatch.setattr("dispute_copilot.observability.board.load_view", lambda *_a, **_k: {})
    monkeypatch.setattr("dispute_copilot.observability.board.render", lambda *_a, **_k: "<p>ok</p>")
    monkeypatch.setattr("dispute_copilot.observability.compare_page.load_comparison", lambda *_a, **_k: {})
    monkeypatch.setattr("dispute_copilot.observability.compare_page.render_comparison", lambda *_a, **_k: "<p>c</p>")
    monkeypatch.setattr("dispute_copilot.observability.board.bind_page", lambda *_a, **_k: FakeServer())
    assert main(["dashboard", "--port", "8754"]) == 0
    assert closed["done"] is True
    assert "http://127.0.0.1:8754" in capsys.readouterr().out


def test_dashboard_reports_a_bind_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))
    monkeypatch.setattr("dispute_copilot.observability.board.load_view", lambda *_a, **_k: {})
    monkeypatch.setattr("dispute_copilot.observability.board.render", lambda *_a, **_k: "<p>ok</p>")
    monkeypatch.setattr("dispute_copilot.observability.compare_page.load_comparison", lambda *_a, **_k: {})
    monkeypatch.setattr("dispute_copilot.observability.compare_page.render_comparison", lambda *_a, **_k: "<p>c</p>")

    def boom(*_a, **_k):
        raise OSError("address already in use")

    monkeypatch.setattr("dispute_copilot.observability.board.bind_page", boom)
    assert main(["dashboard", "--port", "8754"]) == 1
    assert "could not listen" in capsys.readouterr().err


def test_a_bad_data_dir_exits_before_the_mock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("LLM_PROVIDER", "null")
    data = tmp_path / "data"
    (data / "documents").mkdir(parents=True)
    (data / "documents" / "a.md").write_text("no source header\n", encoding="utf-8")
    assert main(["--data-dir", str(data), "--submissions", str(tmp_path / "sub"), "run", "CARD-D01"]) == 1
    assert "Source ID" in capsys.readouterr().err


def test_summary_copies_the_scored_fields(tmp_path: Path):
    path = tmp_path / "v1.json"
    body = _draft()
    assert _summary(body, path) == {
        "draft": str(path),
        CASE_ID: "CARD-D01",
        CLASSIFICATION: "unauthorized",
        ROUTING: "ready_for_review",
        POLICY_IDS: ["CARD-POLICY-V2", "CARD-PROCEDURE-01"],
        DEADLINE: None,
        FACTS: {"transaction_id": "TX-D01"},
    }


def test_traced_case_writes_a_draft_and_records_a_skip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_PROVIDER", "null")
    monkeypatch.setenv("FEATURE_LLM_REVIEW", "off")
    settings = _settings(tmp_path)
    tools = _tools()
    draft, path = _traced_case(
        "CARD-D01",
        settings,
        FileCorpus(DATA),
        tools,
        FakeCaseSystem(),
        None,
        "run-cli",
        case=tools.list_cases()[0],
        approach="1",
    )
    assert path.is_file()
    assert draft[CASE_ID] == "CARD-D01"
    sink: list[dict] = []
    with pytest.raises(OutsideCheckpoint):
        _traced_case(
            "NOPE",
            settings,
            FileCorpus(DATA),
            FakeToolClient(
                cases=[{"case_id": "NOPE", "intake_source": "NOPE-INTAKE", "documents": ["NOPE-INTAKE"]}],
                transactions={},
                merchants={},
            ),
            FakeCaseSystem(),
            None,
            "run-cli",
            case={"case_id": "NOPE", "intake_source": "NOPE-INTAKE", "documents": ["NOPE-INTAKE"]},
            sink=sink,
        )
    assert sink
    assert any(row.get("attrs", {}).get("outcome") == "not_written" for row in sink)


def test_run_all_report_skips_unusable_ids_and_failed_cases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_PROVIDER", "null")
    monkeypatch.setenv("FEATURE_LLM_REVIEW", "off")
    settings = _settings(tmp_path)
    good = _tools("CARD-D01")
    tools = FakeToolClient(
        cases=[
            {"case_id": "../bad"},
            good.list_cases()[0],
            {"case_id": "NOPE", "intake_source": "NOPE-INTAKE", "documents": ["NOPE-INTAKE"]},
        ],
        transactions={"CARD-D01": [(200, {
            "source_id": "CARD-D01-TRANSACTIONS",
            "transactions": [
                {
                    "transaction_id": "TX-D01",
                    "amount": 117,
                    "currency": "XCR",
                    "posted_date": "2026-08-19",
                    "merchant_id": "MER-MORROW",
                }
            ],
        })]},
        merchants={"CARD-D01": [(200, {"source_id": "MERCHANT", "display_name": "Morrow Supplies", "note": "ok"})]},
    )
    report = run_all_report(settings, FileCorpus(DATA), tools, FakeCaseSystem(), None, "run-all")
    assert len(report["written"]) == 1
    assert report["written"][0][CASE_ID] == "CARD-D01"
    reasons = {row[CASE_ID]: row["reason"] for row in report["not_written"]}
    assert "../bad" in reasons
    assert "NOPE" in reasons
    assert report["alerts_fired"] is not None


def test_run_one_and_run_all_print_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    monkeypatch.setenv("LLM_PROVIDER", "null")
    settings = _settings(tmp_path)
    args = Namespace(approach="1", shadow=False)
    tools = _tools()
    monkeypatch.setattr(
        "dispute_copilot.cli._traced_case",
        lambda *a, **k: (_draft(), tmp_path / "v1.json"),
    )
    assert _run_one("CARD-D01", settings, FileCorpus(DATA), tools, FakeCaseSystem(), None, "r", args) == 0
    assert json.loads(capsys.readouterr().out)[CASE_ID] == "CARD-D01"
    monkeypatch.setattr(
        "dispute_copilot.cli.run_all_report",
        lambda *a, **k: {"written": [_summary(_draft(), tmp_path / "v1.json")], "not_written": [{"case_id": "X", "reason": "skip"}]},
    )
    assert _run_all(settings, FileCorpus(DATA), tools, FakeCaseSystem(), None, "r", args) == 2


def test_compare_lists_one_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    monkeypatch.setenv("LLM_PROVIDER", "null")
    settings = _settings(tmp_path)
    tools = _tools()
    args = Namespace(approaches=" , ", case="CARD-D01")
    monkeypatch.setattr(
        "dispute_copilot.cli.compare_approaches",
        lambda *a, **k: [
            {
                CASE_ID: "CARD-D01",
                "approach": "1",
                "final_class": "unauthorized",
                "route": "ready_for_review",
                "missing": [],
                "path": "rules_only",
            }
        ],
    )
    assert _compare(args, settings, FileCorpus(DATA), tools) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[0].startswith("case\tapproach")
    assert "CARD-D01\t1\tunauthorized\tready_for_review\t-\trules_only" in lines


def test_compare_walks_the_listed_cases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    settings = _settings(tmp_path)
    tools = FakeToolClient(
        cases=[{"case_id": "CARD-D01"}, {"case_id": "bad/id"}, "x"],
        transactions={},
        merchants={},
    )
    seen: list[str] = []

    def fake_compare(case_id, **kwargs):
        seen.append(case_id)
        return [{CASE_ID: case_id, "approach": "1", "final_class": "x", "route": "y", "missing": ["a"], "path": "p"}]

    monkeypatch.setattr("dispute_copilot.cli.compare_approaches", fake_compare)
    assert _compare(Namespace(approaches="1", case=None), settings, FileCorpus(DATA), tools) == 0
    assert seen == ["CARD-D01"]
    assert "CARD-D01\t1\tx\ty\ta\tp" in capsys.readouterr().out


def test_review_commands_print_the_handle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    settings = _settings(tmp_path)
    write_draft(settings.submissions_dir, "CARD-D01", _draft())
    monkeypatch.setattr(
        "dispute_copilot.cli.record_agent_review",
        lambda *a, **k: {"passed": True, "case_id": "CARD-D01"},
    )
    monkeypatch.setattr("dispute_copilot.cli.format_reviewer_note", lambda *a, **k: "note")
    assert _review_command("agent-review", "CARD-D01", settings, FakeCaseSystem()) == 0
    assert json.loads(capsys.readouterr().out)["passed"] is True
    monkeypatch.setattr(
        "dispute_copilot.cli.record_agent_review",
        lambda *a, **k: {"passed": False, "case_id": "CARD-D01"},
    )
    assert _review_command("agent-review", "CARD-D01", settings, FakeCaseSystem()) == 2
    capsys.readouterr()
    monkeypatch.setattr(
        "dispute_copilot.cli.submit_review",
        lambda *a, **k: {"review_id": "rev_1", "agent_approval": {"passed": True}},
    )
    assert _review_command("submit-review", "CARD-D01", settings, FakeCaseSystem()) == 0
    assert json.loads(capsys.readouterr().out)["review_id"] == "rev_1"
    monkeypatch.setattr("dispute_copilot.cli.create_record", lambda *a, **k: {"record_id": "rec-1", "posted": True})
    assert _review_command("create-record", "CARD-D01", settings, FakeCaseSystem()) == 0
    assert json.loads(capsys.readouterr().out)["record_id"] == "rec-1"


def test_review_command_maps_expected_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("LLM_PROVIDER", "null")
    monkeypatch.setenv("FEATURE_LLM_REVIEW", "off")
    data = tmp_path / "data"
    (data / "documents").mkdir(parents=True)
    (data / "documents" / "ok.md").write_text("Source ID: ONLY\n# x\nhello\n", encoding="utf-8")

    class Healthy:
        def __init__(self, *a, **k):
            pass

        def health(self):
            return {"status": "ok"}

    monkeypatch.setattr("dispute_copilot.cli.HttpToolClient", Healthy)
    monkeypatch.setattr("dispute_copilot.cli.HttpCaseSystem", lambda *a, **k: FakeCaseSystem())
    monkeypatch.setattr("dispute_copilot.cli.build_drafter", lambda: None)

    def missing(*_a, **_k):
        raise FileNotFoundError("no draft has been written for CARD-D01")

    monkeypatch.setattr("dispute_copilot.cli.latest_draft", missing)
    assert main(["--data-dir", str(data), "--submissions", str(tmp_path / "sub"), "agent-review", "CARD-D01"]) == 2
    assert "no draft" in capsys.readouterr().err

    def refused(*_a, **_k):
        raise CreateRejected(403, {"error": "approval_gate"})

    monkeypatch.setattr("dispute_copilot.cli.latest_draft", lambda *a, **k: (tmp_path / "v1.json", _draft(), 1))
    monkeypatch.setattr("dispute_copilot.cli.create_record", refused)
    assert main(["--data-dir", str(data), "--submissions", str(tmp_path / "sub"), "create-record", "CARD-D01"]) == 2
    assert "was not created" in capsys.readouterr().err


def test_review_page_starts_uvicorn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    called = {}

    def fake_run(app, host, port):
        called["host"] = host
        called["port"] = port
        called["app"] = app

    monkeypatch.setattr("uvicorn.run", fake_run)
    settings = _settings(tmp_path)
    assert _serve_review_page(settings, FakeCaseSystem(), FileCorpus(DATA), _tools(), tmp_path / "run.sqlite") == 0
    assert called["host"] == "127.0.0.1"
    assert called["port"] == 8080


def test_main_dispatches_after_a_healthy_mock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("LLM_PROVIDER", "null")
    data = tmp_path / "data"
    (data / "documents").mkdir(parents=True)
    (data / "documents" / "ok.md").write_text("Source ID: ONLY\n# x\nhello\n", encoding="utf-8")

    class Healthy:
        def __init__(self, *a, **k):
            pass

        def health(self):
            return {"status": "ok"}

    monkeypatch.setattr("dispute_copilot.cli.HttpToolClient", Healthy)
    monkeypatch.setattr("dispute_copilot.cli.HttpCaseSystem", lambda *a, **k: FakeCaseSystem())
    monkeypatch.setattr("dispute_copilot.cli.build_drafter", lambda: None)
    monkeypatch.setattr("dispute_copilot.cli._run_one", lambda *a, **k: 0)
    monkeypatch.setattr("dispute_copilot.cli._run_all", lambda *a, **k: 0)
    monkeypatch.setattr("dispute_copilot.cli._compare", lambda *a, **k: 0)
    monkeypatch.setattr("dispute_copilot.cli._serve_review_page", lambda *a, **k: 0)
    monkeypatch.setattr("dispute_copilot.cli._review_command", lambda *a, **k: 0)
    common = ["--mock-url", "http://127.0.0.1:8765", "--data-dir", str(data), "--submissions", str(tmp_path / "sub")]
    assert main([*common, "run", "CARD-D01"]) == 0
    assert main([*common, "run-all"]) == 0
    assert main([*common, "compare"]) == 0
    assert main([*common, "review-page"]) == 0
    assert main([*common, "agent-review", "CARD-D01"]) == 0

    def down(*_a, **_k):
        from dispute_copilot.errors import MockUnavailable

        raise MockUnavailable("down")

    monkeypatch.setattr("dispute_copilot.cli._run_one", down)
    assert main([*common, "run", "CARD-D01"]) == 1


def test_run_all_report_reraises_a_down_mock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    settings = _settings(tmp_path)
    tools = FakeToolClient(cases=[{"case_id": "CARD-D01"}], transactions={}, merchants={})

    def down(*_a, **_k):
        from dispute_copilot.errors import MockUnavailable

        raise MockUnavailable("down")

    monkeypatch.setattr("dispute_copilot.cli._traced_case", down)
    with pytest.raises(CopilotError):
        run_all_report(settings, FileCorpus(DATA), tools, FakeCaseSystem(), None, "run-all")


def test_review_page_defaults_the_state_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import sys

    seen = {}

    def fake_run(app, host, port):
        del app, host, port
        seen["ok"] = True

    monkeypatch.setattr("uvicorn.run", fake_run)
    filtered = [item for item in sys.path if Path(item).resolve() != ROOT.resolve()]
    monkeypatch.setattr(sys, "path", filtered)
    settings = _settings(tmp_path)
    assert _serve_review_page(settings, FakeCaseSystem(), FileCorpus(DATA), _tools(), None) == 0
    assert seen["ok"] is True
    assert any(Path(item).resolve() == ROOT.resolve() for item in sys.path)
