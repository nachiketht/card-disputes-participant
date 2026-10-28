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


class _CountingBlob:
    def __init__(self, inner: MemoryBlob) -> None:
        self.inner = inner
        self.gets: list[str] = []

    def get(self, name: str):
        self.gets.append(name)
        return self.inner.get(name)

    def list_stats(self, prefix: str):
        return self.inner.list_stats(prefix)

    def list(self, prefix: str):
        return self.inner.list(prefix)


def test_refresh_downloads_only_changed_markdown():
    client = MemoryBlob()
    original = _markdown("A")
    same_length = b"Source ID: A\n\nText for A.\n"
    assert len(original) == len(same_length)
    client.put("documents/a.md", original)
    client.put("policies/b.md", _markdown("B"))
    client.put("notes/ignore.txt", b"nope")
    assert client.list("documents/") == ["documents/a.md"]
    name, size, etag = client.list_stats("documents/")[0]
    assert (name, size) == ("documents/a.md", len(original))
    corpus = BlobCorpus(client)
    counter = _CountingBlob(client)
    corpus._client = counter

    assert corpus.refresh() is None
    assert counter.gets == []

    client.put("policies/b.md", _markdown("B") + b"\nMore.\n")
    assert corpus.refresh() is None
    assert counter.gets == ["policies/b.md"]
    assert "More." in corpus.get("B")
    counter.gets.clear()

    client.put("documents/a.md", same_length)
    assert client.list_stats("documents/")[0][2] != etag
    assert corpus.refresh() is None
    assert counter.gets == ["documents/a.md"]
    assert corpus.get("A").startswith("Source ID: A")
    assert "Text for A." in corpus.get("A")
    counter.gets.clear()

    del client.objects["documents/a.md"]
    assert corpus.refresh() is None
    assert counter.gets == []
    assert corpus.ids() == ["B"]
    with pytest.raises(KeyError):
        corpus.get("A")

    client.put("documents/extra.md", _markdown("B"))
    message = corpus.refresh()
    assert message is not None
    assert "duplicate Source ID" in message
    assert corpus.ids() == ["B"]
    assert "More." in corpus.get("B")
