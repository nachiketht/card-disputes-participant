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


def _read_markdown(client) -> tuple[dict[str, str], tuple[tuple[str, int], ...]]:
    texts: dict[str, str] = {}
    signature: list[tuple[str, int]] = []
    for prefix in _FOLDERS:
        for name in client.list(prefix):
            if not name.endswith(".md"):
                continue
            raw = client.get(name)
            if raw is None:
                continue
            signature.append((name, len(raw)))
            text = raw.decode("utf-8")
            source_id = source_id_of(text)
            if source_id in texts:
                raise ValueError(f"duplicate Source ID {source_id}")
            texts[source_id] = text
    return texts, tuple(signature)


class BlobCorpus:
    def __init__(self, client) -> None:
        self._client = client
        self._texts, self._signature = _read_markdown(client)

    @classmethod
    def from_env(cls) -> "BlobCorpus":
        return cls(_client())

    def refresh(self) -> str | None:
        try:
            texts, signature = _read_markdown(self._client)
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
