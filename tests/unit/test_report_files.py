"""Report files saved for the status page. Drafts and complaints stay out."""

import importlib.util
from pathlib import Path

from dispute_copilot.adapters.blob_store import MemoryBlob

ROOT = Path(__file__).resolve().parents[2]
COMPLAINT = "I found a card purchase at Morrow Supplies"


def _load():
    spec = importlib.util.spec_from_file_location("report_files", ROOT / "tools" / "report_files.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tree(root: Path) -> None:
    (root / "reports" / "traces").mkdir(parents=True)
    (root / "reports" / "traces" / "CARD-S001.jsonl").write_text('{"name":"case"}\n', encoding="utf-8")
    (root / "reports" / "logs").mkdir(parents=True)
    (root / "reports" / "logs" / "run.jsonl").write_text("{}\n", encoding="utf-8")
    (root / "reports" / "logs" / "mcp.jsonl").write_text("{}\n", encoding="utf-8")
    (root / "reports" / "logs" / "other.log").write_text(COMPLAINT, encoding="utf-8")
    (root / "reports" / "audit.json").write_text("[]\n", encoding="utf-8")
    folder = root / "reports" / "classification" / "3"
    folder.mkdir(parents=True)
    (folder / "CARD-S001.json").write_text('{"case_id":"CARD-S001"}\n', encoding="utf-8")
    saved = root / "submissions" / "classification" / "3"
    saved.mkdir(parents=True)
    (saved / "CARD-S001.json").write_text('{"case_id":"CARD-S001"}\n', encoding="utf-8")
    drafts = root / "submissions" / "drafts"
    drafts.mkdir(parents=True)
    (drafts / "CARD-S001.json").write_text(COMPLAINT, encoding="utf-8")


def test_upload_writes_latest_and_skips_a_planted_draft(tmp_path: Path):
    module = _load()
    _tree(tmp_path)
    blob = MemoryBlob()
    module.upload_run(blob, tmp_path, "run-abc123")
    assert blob.get("latest") == b"run-abc123"
    prefix = "runs/run-abc123/"
    names = blob.list(prefix)
    assert prefix + "reports/traces/CARD-S001.jsonl" in names
    assert prefix + "reports/logs/run.jsonl" in names
    assert prefix + "reports/logs/mcp.jsonl" in names
    assert prefix + "reports/audit.json" in names
    assert prefix + "reports/classification/3/CARD-S001.json" in names
    assert prefix + "submissions/classification/3/CARD-S001.json" in names
    stored = b"".join(blob.get(name) or b"" for name in names)
    assert COMPLAINT.encode() not in stored
    assert blob.get("latest") == b"run-abc123"
    assert all("drafts" not in name for name in names)
    assert all(not name.endswith("other.log") for name in names)


def test_download_reads_only_the_latest_prefix(tmp_path: Path):
    module = _load()
    blob = MemoryBlob()
    blob.put("latest", b"run-new")
    blob.put("runs/run-new/reports/traces/CARD-S001.jsonl", b"new\n")
    blob.put("runs/run-old/reports/traces/CARD-S001.jsonl", b"old\n")
    blob.put("runs/run-new/submissions/drafts/CARD-S001.json", COMPLAINT.encode())
    dest = tmp_path / "data"
    assert module.download_latest(blob, dest) == "run-new"
    saved = dest / "reports" / "traces" / "CARD-S001.jsonl"
    assert saved.read_text(encoding="utf-8") == "new\n"
    assert not (dest / "submissions" / "drafts" / "CARD-S001.json").exists()
    assert "old" not in saved.read_text(encoding="utf-8")


def test_a_missing_latest_downloads_nothing(tmp_path: Path):
    module = _load()
    assert module.download_latest(MemoryBlob(), tmp_path / "data") is None
    assert not (tmp_path / "data").exists()
