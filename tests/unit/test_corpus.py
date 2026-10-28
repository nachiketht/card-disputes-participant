"""Markdown corpus loading stays on Source ID headers."""

from pathlib import Path

import pytest

from dispute_copilot.adapters.corpus import FileCorpus, MemoryCorpus, source_id_of


def test_source_id_is_required():
    assert source_id_of("Source ID: CARD-D01-INTAKE\n") == "CARD-D01-INTAKE"
    with pytest.raises(ValueError):
        source_id_of("no header here\n")


def test_memory_corpus_raises_for_an_unknown_id():
    corpus = MemoryCorpus({"A": "text"})
    assert corpus.get("A") == "text"
    assert corpus.ids() == ["A"]
    with pytest.raises(KeyError):
        corpus.get("missing")


def test_file_corpus_refresh_and_duplicate_ids(tmp_path: Path):
    documents = tmp_path / "documents"
    documents.mkdir()
    (documents / "a.md").write_text("Source ID: A\nhello\n", encoding="utf-8")
    corpus = FileCorpus(tmp_path)
    assert corpus.get("A") == "Source ID: A\nhello\n"
    assert corpus.refresh() is None
    (documents / "b.md").write_text("Source ID: B\nsecond\n", encoding="utf-8")
    assert corpus.refresh() is None
    assert "B" in corpus.ids()
    (documents / "c.md").write_text("Source ID: A\ndup\n", encoding="utf-8")
    message = corpus.refresh()
    assert message is not None
    assert "duplicate" in message
    assert corpus.get("B") == "Source ID: B\nsecond\n"
    with pytest.raises(KeyError):
        corpus.get("missing")
    with pytest.raises(ValueError):
        FileCorpus(tmp_path)
