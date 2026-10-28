"""Append-only audit objects. Each event is a new blob. Nothing deletes or replaces one."""

import json
import os
import uuid


def _stamp_from(record: dict) -> str:
    raw = record.get("at")
    text = raw if isinstance(raw, str) else ""
    cleaned = "".join(char if char.isalnum() else "" for char in text)
    return cleaned or "event"


class BlobAuditLog:
    def __init__(self, client, clock=None, ident=None) -> None:
        self._client = client
        self._clock = clock
        self._ident = ident or (lambda: uuid.uuid4().hex)

    @classmethod
    def from_env(cls) -> "BlobAuditLog":
        from dispute_copilot.adapters.blob_store import azure_container

        container = os.environ.get("AUDIT_CONTAINER") or "audit"
        return cls(azure_container(container))

    def append_record(self, record: dict) -> str:
        stamp = self._clock() if self._clock is not None else _stamp_from(record)
        name = f"audit/{stamp}-{self._ident()}.json"
        payload = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self._client.put(name, payload, if_absent=True)
        return name
