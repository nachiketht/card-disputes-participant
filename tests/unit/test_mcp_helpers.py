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
