"""HTTP adapters map mock status codes and fail when the host is down."""

import httpx
import pytest

from dispute_copilot.adapters.case_system import (
    FakeCaseSystem,
    HttpCaseSystem,
    record_from_get,
    record_from_post,
    review_from_get,
    review_from_post,
)
from dispute_copilot.adapters.tools import HttpToolClient
from dispute_copilot.errors import CreateRejected, MockUnavailable


def test_review_and_record_payloads_are_mapped():
    submitted = review_from_post({"review_id": "rev_1", "draft_hash": "abc", "status": "pending"})
    assert submitted.review_id == "rev_1"
    review = review_from_get(
        {"id": "rev_1", "case_id": "CARD-D01", "status": "pending", "hash": "abc", "draft": "{}", "reviewer": "N"}
    )
    assert review.reviewer == "N"
    try:
        review_from_get({"id": "rev_1", "case_id": "CARD-D01", "status": "pending", "hash": "abc", "draft": {}})
    except CreateRejected:
        pass
    else:
        raise AssertionError("a non-string draft was accepted")
    found = record_from_get(200, {"id": "rec-1"})
    assert found.found is True
    missing = record_from_get(404, {"error": "not_found"})
    assert missing.found is False
    try:
        record_from_get(500, {"error": "down"})
    except CreateRejected:
        pass
    else:
        raise AssertionError("a 500 record read was accepted")
    created = record_from_post(201, {"record_id": "rec-1"})
    assert created.record_id == "rec-1"
    conflict = record_from_post(409, {"existing_record_id": "rec-1"})
    assert conflict.existing_record_id == "rec-1"
    try:
        record_from_post(400, {"error": "bad"})
    except CreateRejected:
        pass
    else:
        raise AssertionError("a 400 create was accepted")


def test_http_case_system_reads_and_writes_through_the_client():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.method == "GET" and request.url.path.startswith("/v1/records/"):
            return httpx.Response(200, json={"id": "rec-1"})
        if request.method == "POST" and request.url.path == "/v1/reviews":
            return httpx.Response(201, json={"review_id": "rev_1", "draft_hash": "abc", "status": "pending"})
        if request.method == "GET" and request.url.path.startswith("/v1/reviews/"):
            return httpx.Response(
                200,
                json={"id": "rev_1", "case_id": "CARD-D01", "status": "pending", "hash": "abc", "draft": "{}"},
            )
        if request.method == "POST" and request.url.path == "/v1/records":
            return httpx.Response(201, json={"record_id": "rec-1"})
        return httpx.Response(404, json={"error": "not_found"})

    client = httpx.Client(base_url="https://mock.example", transport=httpx.MockTransport(handler))
    ledger = HttpCaseSystem(client)
    assert ledger.get_record("CARD-D01").found is True
    submitted = ledger.submit_review("CARD-D01", {"case_id": "CARD-D01"})
    assert submitted.review_id == "rev_1"
    review = ledger.get_review("rev_1")
    assert review.case_id == "CARD-D01"
    created = ledger.create_record("CARD-D01", "rev_1", {"case_id": "CARD-D01"}, "key-1")
    assert created.record_id == "rec-1"
    assert "GET /v1/records/CARD-D01" in calls


def test_http_case_system_maps_unreachable_and_non_json():
    def down(request: httpx.Request) -> httpx.Response:
        del request
        raise httpx.ConnectError("down")

    def rejected(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(400, json={"error": "bad"})

    ledger = HttpCaseSystem(httpx.Client(base_url="https://mock.example", transport=httpx.MockTransport(down)))
    with pytest.raises(MockUnavailable):
        ledger.get_record("CARD-D01")

    def not_json_404(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(404, text="nope")

    def not_object_404(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(404, json=["x"])

    ledger = HttpCaseSystem(httpx.Client(base_url="https://mock.example", transport=httpx.MockTransport(not_json_404)))
    assert ledger.get_record("CARD-D01").body == {"error": "not_json"}

    ledger = HttpCaseSystem(httpx.Client(base_url="https://mock.example", transport=httpx.MockTransport(not_object_404)))
    assert ledger.get_record("CARD-D01").body == {"error": "not_an_object"}

    ledger = HttpCaseSystem(httpx.Client(base_url="https://mock.example", transport=httpx.MockTransport(rejected)))
    with pytest.raises(CreateRejected):
        ledger.submit_review("CARD-D01", {})
    with pytest.raises(CreateRejected):
        ledger.get_review("rev_1")


def test_http_tool_client_health_and_case_list_fail_closed():
    def down(request: httpx.Request) -> httpx.Response:
        del request
        raise httpx.ConnectError("down")

    def bad_health(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"status": "down"})

    def bad_list(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"cases": "nope"})

    client = HttpToolClient(httpx.Client(base_url="https://mock.example", transport=httpx.MockTransport(down)))
    with pytest.raises(MockUnavailable):
        client.health()
    with pytest.raises(MockUnavailable):
        client.list_cases()

    client = HttpToolClient(httpx.Client(base_url="https://mock.example", transport=httpx.MockTransport(bad_health)))
    with pytest.raises(MockUnavailable):
        client.health()

    client = HttpToolClient(httpx.Client(base_url="https://mock.example", transport=httpx.MockTransport(bad_list)))
    with pytest.raises(MockUnavailable):
        client.list_cases()


def test_fake_ledger_rejects_a_missing_review():
    ledger = FakeCaseSystem()
    with pytest.raises(CreateRejected):
        ledger.get_review("rev_missing")
