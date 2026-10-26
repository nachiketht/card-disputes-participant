from pathlib import Path

from dispute_copilot.adapters.corpus import FileCorpus


def test_refresh_loads_a_new_file_and_keeps_the_old_text_when_a_file_is_bad(tmp_path: Path):
    policies = tmp_path / "policies"
    documents = tmp_path / "documents"
    policies.mkdir()
    documents.mkdir()
    policy = "Source ID: POL-A\n\nHello\n"
    (policies / "a.md").write_text(policy, encoding="utf-8")
    corpus = FileCorpus(tmp_path)
    loaded = corpus.get("POL-A")
    assert corpus.refresh() is None
    assert corpus.get("POL-A") is loaded

    (documents / "new.md").write_text("Source ID: DOC-B\n\nBody\n", encoding="utf-8")
    assert corpus.refresh() is None
    assert corpus.get("DOC-B").startswith("Source ID: DOC-B")
    assert corpus.get("POL-A") == policy

    (documents / "bad.md").write_text("no header\n", encoding="utf-8")
    assert corpus.refresh()
    assert corpus.get("DOC-B").startswith("Source ID: DOC-B")
    assert corpus.get("POL-A") == policy

    (documents / "bad.md").unlink()
    (documents / "dup.md").write_text("Source ID: DOC-B\n\nOther\n", encoding="utf-8")
    assert "duplicate" in (corpus.refresh() or "")
    assert corpus.get("DOC-B") == "Source ID: DOC-B\n\nBody\n"
