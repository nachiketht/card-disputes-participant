"""MCP wrappers fail closed without a listener. Helpers stay in-process."""

import json
from pathlib import Path

import pytest

from dispute_copilot import mcp_server
from dispute_copilot.errors import CreateRejected, MockUnavailable
from dispute_copilot.storage import write_draft

PAN = "4111111111111111"


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
