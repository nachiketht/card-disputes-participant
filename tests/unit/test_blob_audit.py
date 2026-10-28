"""Append-only audit blobs. Names are unique, and a repeat of the same name fails."""

import json

import pytest

from dispute_copilot.adapters.audit_blob import BlobAuditLog
from dispute_copilot.adapters.blob_store import BlobExists, MemoryBlob


def test_two_events_are_two_blobs():
    client = MemoryBlob()
    log = BlobAuditLog(client)
    first = log.append_record({"at": "t1", "event": "note", "case_id": "CARD-D01", "detail": {"n": 1}})
    second = log.append_record({"at": "t2", "event": "note", "case_id": "CARD-D01", "detail": {"n": 2}})
    assert first != second
    assert first.startswith("audit/")
    assert second.startswith("audit/")
    stored = [json.loads(client.get(name)) for name in client.list("audit/")]
    assert [item["detail"]["n"] for item in stored] == [1, 2]
    assert not hasattr(log, "delete")
    assert not hasattr(client, "delete")


def test_the_same_name_cannot_be_replaced():
    client = MemoryBlob()
    log = BlobAuditLog(client, clock=lambda: "20260101T000000000000Z", ident=lambda: "fixed")
    log.append_record({"at": "t", "event": "note", "case_id": "CARD-D01", "detail": {}})
    with pytest.raises(BlobExists):
        log.append_record({"at": "t", "event": "note", "case_id": "CARD-D01", "detail": {"changed": True}})
    saved = json.loads(client.get("audit/20260101T000000000000Z-fixed.json"))
    assert saved["detail"] == {}
