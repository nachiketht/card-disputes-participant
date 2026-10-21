"""Load policy and document text by the Source ID header.

The data root is configurable. Only the policies and documents trees are read,
and only markdown. The mock fixture file is never opened.
"""

from pathlib import Path


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


class FileCorpus:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self._texts: dict[str, str] = {}
        for folder in ("policies", "documents"):
            root = data_dir / folder
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*.md")):
                text = path.read_text(encoding="utf-8")
                source_id = source_id_of(text)
                if source_id in self._texts:
                    raise ValueError(f"duplicate Source ID {source_id}")
                self._texts[source_id] = text

    def get(self, source_id: str) -> str:
        try:
            return self._texts[source_id]
        except KeyError:
            raise KeyError(source_id) from None

    def ids(self) -> list[str]:
        return sorted(self._texts)
