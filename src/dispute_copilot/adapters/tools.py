"""Read tools. A 503 is retried. Anything else is not an empty history.

The procedure allows three total attempts. The cap is enforced here so a later
config file cannot raise it by accident. POSTs do not go through this client.
"""

from collections.abc import Callable

import httpx

from dispute_copilot.errors import MockUnavailable, ToolFailed

Attempt = Callable[[], tuple[int, object]]
RETRYABLE = frozenset({503})
MAX_ATTEMPTS = 3


def read_with_retries(attempt: Attempt, max_attempts: int = MAX_ATTEMPTS) -> tuple[dict, int]:
    if max_attempts < 1 or max_attempts > MAX_ATTEMPTS:
        raise ValueError(f"max_attempts must be from 1 to {MAX_ATTEMPTS}")
    last_status = 0
    last_body: object = None
    for n in range(1, max_attempts + 1):
        status, body = attempt()
        last_status, last_body = status, body
        if status == 200 and isinstance(body, dict):
            return body, n
        if status not in RETRYABLE:
            raise ToolFailed(status, body, n)
    raise ToolFailed(last_status, last_body, max_attempts)


class HttpToolClient:
    def __init__(self, http: httpx.Client, max_attempts: int = MAX_ATTEMPTS) -> None:
        if max_attempts < 1 or max_attempts > MAX_ATTEMPTS:
            raise ValueError(f"max_attempts must be from 1 to {MAX_ATTEMPTS}")
        self._http = http
        self.max_attempts = max_attempts

    def health(self) -> dict:
        try:
            response = self._http.get("/health")
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise MockUnavailable("mock health check failed") from exc
        if response.status_code != 200 or not isinstance(body, dict) or body.get("status") != "ok":
            raise MockUnavailable("mock health check was not ok")
        return body

    def list_cases(self) -> list[dict]:
        try:
            response = self._http.get("/v1/cases")
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise MockUnavailable("mock case list failed") from exc
        cases = body.get("cases") if isinstance(body, dict) else None
        if response.status_code != 200 or not isinstance(cases, list):
            raise MockUnavailable("mock case list was not usable")
        return cases

    def transactions(self, case_id: str) -> tuple[dict, int]:
        return read_with_retries(
            lambda: self._read(f"/v1/cases/{case_id}/transactions"),
            max_attempts=self.max_attempts,
        )

    def merchant(self, case_id: str) -> tuple[dict, int]:
        return read_with_retries(
            lambda: self._read(f"/v1/cases/{case_id}/merchant"),
            max_attempts=self.max_attempts,
        )

    def _read(self, path: str) -> tuple[int, object]:
        try:
            response = self._http.get(path)
        except httpx.HTTPError:
            return 0, {"error": "transport"}
        try:
            body = response.json()
        except ValueError:
            body = {"error": "not_json"}
        return response.status_code, body


class FakeToolClient:
    """Scripted responses for tests. Each entry is one attempt."""

    def __init__(
        self,
        cases: list[dict],
        transactions: dict[str, list[tuple[int, dict]]],
        merchants: dict[str, list[tuple[int, dict]]],
        max_attempts: int = MAX_ATTEMPTS,
        healthy: bool = True,
    ) -> None:
        if max_attempts < 1 or max_attempts > MAX_ATTEMPTS:
            raise ValueError(f"max_attempts must be from 1 to {MAX_ATTEMPTS}")
        self._cases = cases
        self._transactions = {key: list(value) for key, value in transactions.items()}
        self._merchants = {key: list(value) for key, value in merchants.items()}
        self.max_attempts = max_attempts
        self.healthy = healthy
        self.transaction_calls: list[str] = []
        self.merchant_calls: list[str] = []

    def health(self) -> dict:
        if not self.healthy:
            raise MockUnavailable("fake mock is down")
        return {"status": "ok"}

    def list_cases(self) -> list[dict]:
        return list(self._cases)

    def transactions(self, case_id: str) -> tuple[dict, int]:
        self.transaction_calls.append(case_id)
        script = self._transactions.get(case_id, [])
        return read_with_retries(lambda: _pop(script), max_attempts=self.max_attempts)

    def merchant(self, case_id: str) -> tuple[dict, int]:
        self.merchant_calls.append(case_id)
        script = self._merchants.get(case_id, [])
        return read_with_retries(lambda: _pop(script), max_attempts=self.max_attempts)


def _pop(script: list[tuple[int, dict]]) -> tuple[int, dict]:
    if not script:
        return 404, {"error": "not_found"}
    return script.pop(0)
