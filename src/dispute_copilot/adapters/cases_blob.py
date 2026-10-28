"""Case files in the cases container: markdown corpus, and the mock fixture for the server.

The copilot corpus reads documents and policies only.
"""

import json
import os

from dispute_copilot.adapters.corpus import source_id_of

_FIXTURE = "dataset" + ".json"
_FOLDERS = ("documents/", "policies/")


def _container_name() -> str:
    return os.environ.get("CASES_CONTAINER") or "cases"


def _client(client=None):
    if client is not None:
        return client
    from dispute_copilot.adapters.blob_store import azure_container

    return azure_container(_container_name())


def read_blob_dataset(client=None) -> dict:
    blob = _client(client)
    raw = blob.get(_FIXTURE)
    if raw is None:
        raise FileNotFoundError(_FIXTURE)
    loaded = json.loads(raw)
    if not isinstance(loaded, dict):
        raise ValueError("fixture is not an object")
    return loaded


def _markdown_stats(client) -> tuple[tuple[str, int, str], ...]:
    rows: list[tuple[str, int, str]] = []
    for prefix in _FOLDERS:
        for name, size, etag in client.list_stats(prefix):
            if name.endswith(".md"):
                rows.append((name, int(size), str(etag)))
    return tuple(sorted(rows))


def _text_for(client, name: str) -> str:
    raw = client.get(name)
    if raw is None:
        raise FileNotFoundError(name)
    return raw.decode("utf-8")


def _load_markdown(client) -> tuple[dict[str, str], dict[str, str], tuple[tuple[str, int, str], ...]]:
    """Download every markdown file. Startup uses this once."""
    signature = _markdown_stats(client)
    texts: dict[str, str] = {}
    names: dict[str, str] = {}
    for name, _size, _etag in signature:
        text = _text_for(client, name)
        source_id = source_id_of(text)
        if source_id in texts:
            raise ValueError(f"duplicate Source ID {source_id}")
        texts[source_id] = text
        names[name] = source_id
    return texts, names, signature


class BlobCorpus:
    def __init__(self, client) -> None:
        self._client = client
        self._texts, self._names, self._signature = _load_markdown(client)

    @classmethod
    def from_env(cls) -> "BlobCorpus":
        return cls(_client())

    def refresh(self) -> str | None:
        """Re-download markdown whose name, size, or etag changed.

        An unchanged list leaves the loaded text in place and does not call get.
        A bad file keeps the previous texts and returns the error.
        """
        try:
            signature = _markdown_stats(self._client)
            if signature == self._signature:
                return None
            previous = {name: (size, etag) for name, size, etag in self._signature}
            live = {name for name, _size, _etag in signature}
            names = {name: source_id for name, source_id in self._names.items() if name in live}
            changed: dict[str, str] = {}
            for name, size, etag in signature:
                if previous.get(name) == (size, etag) and name in self._names:
                    continue
                changed[name] = _text_for(self._client, name)
                names[name] = source_id_of(changed[name])
            texts: dict[str, str] = {}
            for name, source_id in names.items():
                if source_id in texts:
                    raise ValueError(f"duplicate Source ID {source_id}")
                if name in changed:
                    texts[source_id] = changed[name]
                else:
                    texts[source_id] = self._texts[self._names[name]]
        except (OSError, ValueError) as exc:
            return str(exc)
        self._texts = texts
        self._names = names
        self._signature = signature
        return None

    def get(self, source_id: str) -> str:
        try:
            return self._texts[source_id]
        except KeyError:
            raise KeyError(source_id) from None

    def ids(self) -> list[str]:
        return sorted(self._texts)
