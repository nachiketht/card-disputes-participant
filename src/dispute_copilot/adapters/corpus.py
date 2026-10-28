"""Load policy and document text by the Source ID header.

The data root is configurable. Only the policies and documents trees are read,
and only markdown. The mock fixture file is never opened.
"""

import os
from pathlib import Path


def open_corpus(data_dir: Path):
    """Files under data_dir unless CASES_BACKEND=blob."""
    if os.environ.get("CASES_BACKEND") == "blob":
        from dispute_copilot.adapters.cases_blob import BlobCorpus

        return BlobCorpus.from_env()
    return FileCorpus(data_dir)


def source_id_of(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("Source ID: "):
            return line.split(": ", 1)[1].strip()
    raise ValueError("a markdown evidence file has no Source ID header")


class MemoryCorpus:
    def __init__(self, texts: dict[str, str]) -> None:
        self._texts = dict(texts)

    def get(self, source_id: str) -> str:
        try:
            return self._texts[source_id]
        except KeyError:
            raise KeyError(source_id) from None

    def ids(self) -> list[str]:
        return sorted(self._texts)


def _read_markdown(data_dir: Path) -> tuple[dict[str, str], tuple[tuple[str, int, int], ...]]:
    texts: dict[str, str] = {}
    signature: list[tuple[str, int, int]] = []
    for folder in ("policies", "documents"):
        root = data_dir / folder
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.md")):
            stat = path.stat()
            signature.append((str(path), stat.st_mtime_ns, stat.st_size))
            text = path.read_text(encoding="utf-8")
            source_id = source_id_of(text)
            if source_id in texts:
                raise ValueError(f"duplicate Source ID {source_id}")
            texts[source_id] = text
    return texts, tuple(signature)


class FileCorpus:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self._texts, self._signature = _read_markdown(data_dir)

    def refresh(self) -> str | None:
        """Re-read markdown when a file was added or changed.

        A bad file keeps the previous texts and returns the error. An unchanged
        tree leaves the loaded text in place.
        """
        try:
            texts, signature = _read_markdown(self.data_dir)
        except (OSError, ValueError) as exc:
            return str(exc)
        if signature == self._signature:
            return None
        self._texts = texts
        self._signature = signature
        return None

    def get(self, source_id: str) -> str:
        try:
            return self._texts[source_id]
        except KeyError:
            raise KeyError(source_id) from None

    def ids(self) -> list[str]:
        return sorted(self._texts)
