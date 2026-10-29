"""The Azure status page listens on every interface and reads the latest run."""

import importlib.util
from pathlib import Path

from dispute_copilot.adapters.blob_store import MemoryBlob

ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location("dashboard_run", ROOT / "tools" / "dashboard_run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_wrapper_binds_every_interface(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("REPORTS_CONTAINER", "reports")
    module = _load()
    blob = MemoryBlob()
    blob.put("latest", b"run-new")
    line = (
        b'{"kind":"span","name":"case","case_id":"CARD-S001","run_id":"run-new",'
        b'"duration_ms":10,"attrs":{"outcome":"drafted"}}\n'
    )
    blob.put("runs/run-new/reports/traces/CARD-S001.jsonl", line)
    bound = {}

    def bind(page, port, compare, host):
        bound["page"] = page
        bound["port"] = port
        bound["compare"] = compare
        bound["host"] = host
        return "server"

    assert module.serve(tmp_path / "data", lambda name: blob, bind) == "server"
    assert bound["host"] == "0.0.0.0"
    assert bound["port"] == 8080
    assert "CARD-S001" in bound["page"]
    assert bound["compare"]


def test_a_missing_latest_still_serves(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("REPORTS_CONTAINER", "reports")
    module = _load()
    bound = {}

    def bind(page, port, compare, host):
        bound["page"] = page
        bound["host"] = host
        bound["port"] = port
        return "server"

    assert module.serve(tmp_path / "data", lambda name: MemoryBlob(), bind) == "server"
    assert bound["host"] == "0.0.0.0"
    assert bound["port"] == 8080
    assert bound["page"]
