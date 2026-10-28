"""MCP wrappers fail closed without a listener. Helpers stay in-process."""

import json
from pathlib import Path

import pytest

from dispute_copilot import mcp_server
from dispute_copilot.adapters.case_system import FakeCaseSystem, ReviewRecord
from dispute_copilot.errors import CreateRejected, MockUnavailable
from dispute_copilot.serialization import digest, hash_canonical
from dispute_copilot.storage import write_draft

PAN = "4111111111111111"
ROOT = Path(__file__).resolve().parents[2]
CUSTOMER = (
    "We have prepared your complaint for review. "
    "We cannot confirm an outcome. "
    "No case has been created yet; human approval remains required."
)
QUOTE = "Use unauthorized when the cardholder did not allow the purchase."


def _draft() -> dict:
    return {
        "case_id": "CARD-D01",
        "classification": "unauthorized",
        "routing": "ready_for_review",
        "policy_ids": ["CARD-POLICY-V2", "CARD-PROCEDURE-01"],
        "facts": {
            "transaction_id": "TX-D01",
            "amount": 117,
            "currency": "XCR",
            "reported_date": "2026-10-01",
        },
        "missing_information": ["authorization_confirmation"],
        "deadline": None,
        "citations": [
            {
                "claim": "classification",
                "source_id": "CARD-PROCEDURE-01",
                "quote": QUOTE,
            }
        ],
        "follow_up_questions": [
            {
                "field": "authorization_confirmation",
                "question": "Please confirm whether this purchase was authorized.",
            }
        ],
        "narrative": f"Case summary: unauthorized complaint for TX-D01. Customer message draft: {CUSTOMER}",
    }


def _mcp_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pytest.importorskip("mcp")
    monkeypatch.setenv("DATA_DIR", str(ROOT / "data" / "dev"))
    monkeypatch.setenv("SUBMISSIONS_DIR", str(tmp_path / "submissions"))
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("MOCK_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("LLM_PROVIDER", "null")
    monkeypatch.setenv("FEATURE_LLM_REVIEW", "off")
    (tmp_path / "submissions").mkdir()
    (tmp_path / "reports").mkdir()


def _stub_http(monkeypatch: pytest.MonkeyPatch, ledger: FakeCaseSystem | None = None) -> FakeCaseSystem:
    held = ledger or FakeCaseSystem()

    class Remote:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        def health(self):
            return {"status": "ok"}

    monkeypatch.setattr(mcp_server, "HttpToolClient", Remote)
    monkeypatch.setattr(mcp_server, "HttpCaseSystem", lambda *args, **kwargs: held)
    return held


def test_invalid_case_ids_are_rejected():
    try:
        mcp_server._require_case("../CARD-D01")
    except mcp_server.CallFailed as exc:
        assert exc.code == "invalid_case_id"
    else:
        raise AssertionError("a path segment was accepted")
    try:
        mcp_server._require_case(PAN)
    except mcp_server.CallFailed as exc:
        assert exc.code == "invalid_case_id"
    else:
        raise AssertionError("a restricted case id was accepted")


def test_public_error_bodies_mask_restricted_text():
    text = mcp_server._public_body("rejected", f"card {PAN}")
    assert PAN not in text
    assert json.loads(text)["code"] == "rejected"
    assert mcp_server._error_code({"error": "not_found"}) == "not_found"
    assert mcp_server._error_code("nope") == "rejected"


def test_failed_calls_map_known_errors():
    failed = mcp_server._as_failed(CreateRejected(409, {"error": "duplicate"}))
    assert failed.code == "duplicate"
    assert failed.status == 409
    missing = mcp_server._as_failed(FileNotFoundError("gone"))
    assert missing.code == "not_found"
    down = mcp_server._as_failed(MockUnavailable("down"))
    assert down.code == "mock_unavailable"
    other = mcp_server._as_failed(RuntimeError("boom"))
    assert other.code == "rejected"
    same = mcp_server._as_failed(failed)
    assert same is failed


def test_draft_quotes_are_marked_untrusted():
    body = mcp_server._present(
        {
            "narrative": "Customer message draft.",
            "citations": [{"claim": "rule", "source_id": "CARD-PROCEDURE-01", "quote": "Use unauthorized."}],
        }
    )
    assert body["narrative"]["untrusted"] is True
    assert body["citations"][0]["quote"]["untrusted"] is True
    assert body["citations"][0]["quote"]["text"] == "Use unauthorized."


def test_approach_arg_rejects_unknown_names():
    assert mcp_server._approach_arg(None) is None
    assert mcp_server._approach_arg("2") == "2"
    try:
        mcp_server._approach_arg("9")
    except mcp_server.CallFailed as exc:
        assert exc.code == "rejected"
    else:
        raise AssertionError("an unknown approach was accepted")


def test_missing_policy_doc_is_not_found():
    try:
        mcp_server._read_doc("missing.md")
    except mcp_server.CallFailed as exc:
        assert exc.code == "not_found"
    else:
        raise AssertionError("a missing document was returned")
    text = mcp_server._read_doc("POLICY_GUIDE.md")
    assert "Read every file under data/dev/policies" in text


def test_get_draft_and_metrics_use_the_saved_files(tmp_path: Path, monkeypatch):
    pytest.importorskip("mcp")
    monkeypatch.setenv("DATA_DIR", str(Path(__file__).resolve().parents[2] / "data" / "dev"))
    monkeypatch.setenv("SUBMISSIONS_DIR", str(tmp_path / "submissions"))
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    try:
        mcp_server.get_draft("../nope")
    except mcp_server.ToolError:
        pass
    else:
        raise AssertionError("an invalid case id was accepted")
    write_draft(
        tmp_path / "submissions",
        "CARD-D01",
        {
            "case_id": "CARD-D01",
            "classification": "unauthorized",
            "routing": "ready_for_review",
            "narrative": "Customer message draft.",
            "citations": [{"claim": "rule", "source_id": "CARD-PROCEDURE-01", "quote": "Use unauthorized."}],
        },
    )
    saved = mcp_server.get_draft("CARD-D01")
    assert saved["draft_version"] == 1
    assert saved["draft"]["narrative"]["untrusted"] is True
    report = mcp_server.metrics()
    assert "metrics" in report
    traced = mcp_server.explain_trace("CARD-D01")
    assert "spans" in traced
    guide = mcp_server.read_policy_guide()
    assert "Read every file under data/dev/policies" in guide
    contract = mcp_server.read_output_contract()
    assert "Evaluation output contract" in contract


def test_tool_and_resource_wrappers_fail_closed(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    with pytest.raises(mcp_server.ToolError):
        mcp_server._tool_call("get_draft", "../nope", lambda _cid: {})
    with pytest.raises(mcp_server.ToolError):
        mcp_server._tool_call("ping", "CARD-D01", lambda _cid: "not-a-dict")
    with pytest.raises(mcp_server.ToolError):
        mcp_server._tool_call("boom", "CARD-D01", lambda _cid: (_ for _ in ()).throw(RuntimeError("x")))
    assert mcp_server._tool_call("ok", "CARD-D01", lambda cid: {"case_id": cid}) == {"case_id": "CARD-D01"}
    assert mcp_server._tool_call("metrics", None, lambda name: {"ok": True, "name": name})["ok"] is True
    with pytest.raises(mcp_server.ResourceError):
        mcp_server._resource_call("doc", lambda: {"nope": 1})
    with pytest.raises(mcp_server.ResourceError):
        mcp_server._resource_call("doc", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert mcp_server._resource_call("doc", lambda: "hello") == "hello"
    failed = mcp_server.CallFailed("rejected", "nope")
    mcp_server._fail_traced("CARD-D01", "get_draft", failed)
    body = mcp_server._present({"narrative": 1, "citations": ["skip", {"quote": 1}]})
    assert body["narrative"] == 1
    assert body["citations"][0] == "skip"


def test_build_server_registers_tools_and_resources():
    pytest.importorskip("mcp")
    server = mcp_server.build_server()
    assert server is not None


def test_mcp_main_starts_stdio(monkeypatch, tmp_path: Path):
    pytest.importorskip("mcp")
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))
    monkeypatch.setattr(mcp_server.sys, "argv", ["mcp"])
    ran = {"ok": False}

    class Fake:
        async def run_stdio_async(self):
            return None

    monkeypatch.setattr(mcp_server, "build_server", lambda: Fake())
    import anyio

    monkeypatch.setattr(anyio, "run", lambda fn: ran.__setitem__("ok", True))
    assert mcp_server.main() == 0
    assert ran["ok"] is True


def test_read_doc_masks_restricted_text(monkeypatch, tmp_path: Path):
    secret = "SYNTHETIC-" + "PRIVATE-X"
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "POLICY_GUIDE.md").write_text(f"guide {secret}\n", encoding="utf-8")
    monkeypatch.setattr(mcp_server, "_ROOT", tmp_path)
    monkeypatch.setattr(mcp_server, "_DOCS", {"POLICY_GUIDE.md": "guide://policy"})
    text = mcp_server._read_doc("POLICY_GUIDE.md")
    assert secret not in text
    with pytest.raises(mcp_server.CallFailed):
        mcp_server._read_doc("POLICY_GUIDE.md") if False else mcp_server._read_doc("missing.md")
    (docs / "POLICY_GUIDE.md").unlink()
    with pytest.raises(mcp_server.CallFailed):
        mcp_server._read_doc("POLICY_GUIDE.md")


def test_public_error_bodies_replace_a_restricted_code():
    marker = "SYNTHETIC-" + "PRIVATE-CODE"
    text = mcp_server._public_body(marker, "ok")
    body = json.loads(text)
    assert body["code"] == "rejected"
    assert body["message"] == "[redacted]"
    assert marker not in text


def test_tool_and_resource_errors_are_re_raised():
    pytest.importorskip("mcp")

    def tool_boom(_cid: str):
        raise mcp_server.ToolError("already a tool error")

    with pytest.raises(mcp_server.ToolError, match="already a tool error"):
        mcp_server._tool_call("ping", "CARD-D01", tool_boom)

    def resource_boom():
        raise mcp_server.ResourceError("already a resource error")

    with pytest.raises(mcp_server.ResourceError, match="already a resource error"):
        mcp_server._resource_call("doc", resource_boom)


def test_build_server_requires_the_mcp_extra(monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("mcp")
    monkeypatch.setattr(mcp_server, "MCPServer", None)
    with pytest.raises(RuntimeError, match="mcp extra"):
        mcp_server.build_server()
    monkeypatch.setattr(mcp_server.sys, "argv", ["mcp", "--help"])
    assert mcp_server.main() == 2
    monkeypatch.setattr(mcp_server.sys, "argv", ["mcp"])
    assert mcp_server.main() == 1


def test_clients_health_check_then_yields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _mcp_env(monkeypatch, tmp_path)
    ledger = _stub_http(monkeypatch)
    with mcp_server._clients() as (settings, corpus, remote, bound, drafter):
        assert settings.submissions_dir == tmp_path / "submissions"
        assert remote.health()["status"] == "ok"
        assert bound is ledger
        assert drafter is None
        assert "CARD-PROCEDURE-01" in corpus.ids()


def test_run_case_and_run_all_use_the_local_clients(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _mcp_env(monkeypatch, tmp_path)
    _stub_http(monkeypatch)
    saved = _draft()
    path = tmp_path / "submissions" / "drafts" / "CARD-D01" / "v1.json"
    path.parent.mkdir(parents=True)
    path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(mcp_server, "_traced_case", lambda *args, **kwargs: (saved, path))
    one = mcp_server.run_case("CARD-D01", approach="1", shadow=True)
    assert one["case_id"] == "CARD-D01"
    assert one["classification"] == "unauthorized"
    assert str(path) == one["draft"]
    monkeypatch.setattr(
        mcp_server,
        "run_all_report",
        lambda *args, **kwargs: {"written": ["CARD-D01"], "not_written": []},
    )
    listed = mcp_server.run_all(approach="", shadow=False)
    assert listed["written"] == ["CARD-D01"]
    with pytest.raises(mcp_server.ToolError):
        mcp_server.run_case("../nope")


def test_agent_review_submit_status_and_create_use_the_saved_draft(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _mcp_env(monkeypatch, tmp_path)

    class Tracking(FakeCaseSystem):
        def submit_review(self, case_id, draft):
            submitted = super().submit_review(case_id, draft)
            raw = hash_canonical(draft)
            self.reviews[submitted.review_id] = ReviewRecord(
                submitted.review_id,
                case_id,
                "pending",
                digest(draft),
                raw,
                None,
            )
            return submitted

    ledger = _stub_http(monkeypatch, Tracking())
    write_draft(tmp_path / "submissions", "CARD-D01", _draft())
    approval = mcp_server.agent_review("CARD-D01")
    assert approval["passed"] is True
    assert approval["case_id"] == "CARD-D01"
    submitted = mcp_server.submit_review("CARD-D01")
    assert submitted["review_id"] in ledger.reviews
    status = mcp_server.review_status("CARD-D01")
    assert status["status"] == "pending"
    assert status["reviewer_present"] is False

    saved = _draft()
    raw = hash_canonical(saved)
    hashed = digest(saved)
    ready = FakeCaseSystem()
    ready.reviews["rev_1"] = ReviewRecord("rev_1", "CARD-D01", "approved", hashed, raw, "Ada")
    _stub_http(monkeypatch, ready)
    handle = tmp_path / "submissions" / "reviews" / "CARD-D01.json"
    handle.write_text(
        json.dumps(
            {
                "review_id": "rev_1",
                "draft_hash": hashed,
                "draft_version": 1,
                "agent_approval": {
                    "case_id": "CARD-D01",
                    "draft_version": 1,
                    "draft_hash": hashed,
                    "passed": True,
                    "checks": {"contract_valid": True},
                },
            }
        ),
        encoding="utf-8",
    )
    created = mcp_server.create_record("CARD-D01")
    assert created["posted"] is True
    assert created["record_id"] == "record-CARD-D01"


def test_explain_trace_skips_junk_and_other_cases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _mcp_env(monkeypatch, tmp_path)
    traces = tmp_path / "reports" / "traces"
    traces.mkdir(parents=True)
    (traces / "CARD-D01.jsonl").write_text(
        "\n"
        + "{not json\n"
        + json.dumps({"kind": "event", "name": "note"})
        + "\n"
        + json.dumps({"kind": "span", "name": "case", "duration_ms": 4, "status": "ok"})
        + "\n",
        encoding="utf-8",
    )
    (traces / "CARD-D01.extra.jsonl").write_text(
        json.dumps({"kind": "span", "name": "draft.write", "duration_ms": 1, "status": "ok"}) + "\n",
        encoding="utf-8",
    )
    (traces / "OTHER.jsonl").write_text(
        json.dumps({"kind": "span", "name": "case", "duration_ms": 9, "status": "ok"}) + "\n",
        encoding="utf-8",
    )
    traced = mcp_server.explain_trace("CARD-D01")
    names = [item["name"] for item in traced["spans"]]
    assert names == ["draft.write", "case"]
