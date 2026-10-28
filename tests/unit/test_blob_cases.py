"""Blob cases corpus and fixture load. The client is in memory."""

import json

import pytest

from dispute_copilot.adapters.blob_store import MemoryBlob, azure_container
from dispute_copilot.adapters.cases_blob import BlobCorpus, _FIXTURE, read_blob_dataset
from dispute_copilot.adapters.corpus import FileCorpus, open_corpus


def _markdown(source_id: str) -> bytes:
    return f"Source ID: {source_id}\n\nBody for {source_id}.\n".encode()


def test_open_corpus_stays_on_files(tmp_path):
    (tmp_path / "policies").mkdir()
    corpus = open_corpus(tmp_path)
    assert isinstance(corpus, FileCorpus)


def test_dataset_and_markdown_round_trip():
    client = MemoryBlob()
    client.put(_FIXTURE, json.dumps({"name": "tiny", "cases": []}).encode())
    client.put("documents/intake.md", _markdown("CARD-D01-INTAKE"))
    client.put("policies/rules.md", _markdown("POL-1"))
    client.put("notes/ignore.txt", b"nope")
    loaded = read_blob_dataset(client)
    corpus = BlobCorpus(client)
    assert loaded["name"] == "tiny"
    assert corpus.ids() == ["CARD-D01-INTAKE", "POL-1"]
    assert "tiny" not in corpus.get("POL-1")
    assert corpus.get("CARD-D01-INTAKE").startswith("Source ID: CARD-D01-INTAKE")
    assert corpus.refresh() is None


def test_duplicate_source_id_is_rejected():
    client = MemoryBlob()
    client.put("documents/a.md", _markdown("POL-1"))
    client.put("policies/b.md", _markdown("POL-1"))
    with pytest.raises(ValueError, match="duplicate Source ID"):
        BlobCorpus(client)


def test_missing_fixture_and_missing_source():
    client = MemoryBlob()
    client.put("policies/rules.md", _markdown("POL-1"))
    corpus = BlobCorpus(client)
    with pytest.raises(FileNotFoundError):
        read_blob_dataset(client)
    with pytest.raises(KeyError):
        corpus.get("missing")


def test_blob_client_fails_closed_without_a_driver(monkeypatch):
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT", raising=False)
    with pytest.raises(RuntimeError):
        azure_container("cases")
