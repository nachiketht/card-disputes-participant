"""Case ledger reads and the two writes the procedure allows.

Review submission and record creation are separate from drafting. This module
maps the mock's field names onto one shape. It has no approve operation.
"""

from dataclasses import dataclass

import httpx

from dispute_copilot.errors import CreateRejected, MockUnavailable
from dispute_copilot.fields import CASE_ID


@dataclass(frozen=True)
class RecordLookup:
    found: bool
    record_id: str | None
    http_status: int
    body: dict


@dataclass(frozen=True)
class ReviewRecord:
    review_id: str
    case_id: str
    status: str
    draft_hash: str
    draft_raw: str
    reviewer: str | None


@dataclass(frozen=True)
class SubmittedReview:
    review_id: str
    draft_hash: str
    status: str


@dataclass(frozen=True)
class CreateResult:
    http_status: int
    record_id: str | None
    existing_record_id: str | None
    body: dict


def review_from_post(payload: dict) -> SubmittedReview:
    return SubmittedReview(
        review_id=str(payload["review_id"]),
        draft_hash=str(payload["draft_hash"]),
        status=str(payload["status"]),
    )


def review_from_get(payload: dict) -> ReviewRecord:
    raw = payload.get("draft")
    if not isinstance(raw, str):
        raise CreateRejected(400, {"error": "draft_not_a_string"})
    return ReviewRecord(
        review_id=str(payload["id"]),
        case_id=str(payload[CASE_ID]),
        status=str(payload["status"]),
        draft_hash=str(payload["hash"]),
        draft_raw=raw,
        reviewer=payload.get("reviewer"),
    )


def record_from_get(status: int, payload: dict) -> RecordLookup:
    if status == 200 and payload.get("id"):
        return RecordLookup(True, str(payload["id"]), status, payload)
    if status == 404:
        return RecordLookup(False, None, status, payload)
    raise CreateRejected(status, payload)


def record_from_post(status: int, payload: dict) -> CreateResult:
    if status in (200, 201):
        record_id = payload.get("record_id")
        return CreateResult(status, None if record_id is None else str(record_id), None, payload)
    if status == 409:
        existing = payload.get("existing_record_id")
        return CreateResult(status, None, None if existing is None else str(existing), payload)
    raise CreateRejected(status, payload)


class HttpCaseSystem:
    def __init__(self, http: httpx.Client) -> None:
        self._http = http

    def get_record(self, case_id: str) -> RecordLookup:
        status, body = self._request("GET", f"/v1/records/{case_id}")
        return record_from_get(status, body)

    def submit_review(self, case_id: str, draft: dict) -> SubmittedReview:
        status, body = self._request("POST", "/v1/reviews", {CASE_ID: case_id, "draft": draft})
        if status != 201:
            raise CreateRejected(status, body)
        return review_from_post(body)

    def get_review(self, review_id: str) -> ReviewRecord:
        status, body = self._request("GET", f"/v1/reviews/{review_id}")
        if status != 200:
            raise CreateRejected(status, body)
        return review_from_get(body)

    def create_record(self, case_id: str, review_id: str, draft: dict, idempotency_key: str) -> CreateResult:
        status, body = self._request(
            "POST",
            "/v1/records",
            {CASE_ID: case_id, "review_id": review_id, "draft": draft},
            {"Idempotency-Key": idempotency_key},
        )
        return record_from_post(status, body)

    def _request(self, method: str, path: str, payload: dict | None = None, headers: dict | None = None) -> tuple[int, dict]:
        try:
            response = self._http.request(method, path, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise MockUnavailable("mock case ledger is unreachable") from exc
        try:
            body = response.json()
        except ValueError:
            body = {"error": "not_json"}
        if not isinstance(body, dict):
            body = {"error": "not_an_object"}
        return response.status_code, body


class FakeCaseSystem:
    """In-memory ledger for tests. It records posts and does not approve."""

    def __init__(self) -> None:
        self.records: dict[str, dict] = {}
        self.reviews: dict[str, ReviewRecord] = {}
        self.posts: list[dict] = []
        self.submitted: list[dict] = []
        self.next_id = 1

    def get_record(self, case_id: str) -> RecordLookup:
        row = self.records.get(case_id)
        if row is None:
            return RecordLookup(False, None, 404, {"error": "not_found"})
        return RecordLookup(True, str(row["id"]), 200, row)

    def submit_review(self, case_id: str, draft: dict) -> SubmittedReview:
        self.submitted.append(draft)
        review_id = f"rev_{self.next_id}"
        self.next_id += 1
        return SubmittedReview(review_id, "pending-hash", "pending")

    def get_review(self, review_id: str) -> ReviewRecord:
        try:
            return self.reviews[review_id]
        except KeyError:
            raise CreateRejected(404, {"error": "not_found"}) from None

    def create_record(self, case_id: str, review_id: str, draft: dict, idempotency_key: str) -> CreateResult:
        self.posts.append({CASE_ID: case_id, "review_id": review_id, "draft": draft, "key": idempotency_key})
        record_id = f"record-{case_id}"
        self.records[case_id] = {"id": record_id, CASE_ID: case_id}
        return CreateResult(201, record_id, None, {"record_id": record_id})
