"""Read tools. A 503 or a request timeout is retried. Anything else is not an empty history.

The procedure allows three total attempts per case. The cap is enforced here
and in the retry policy so a later config file cannot raise it by accident.
Waits between attempts use exponential backoff with capped jitter. A
cross-case circuit breaker per dependency can skip a call only after earlier
cases exhausted every attempt; it never cuts attempts inside a case. POSTs do
not go through this client.
"""

from collections.abc import Callable

import httpx

from dispute_copilot.errors import BreakerOpen, MockUnavailable, ToolFailed
from dispute_copilot.observability.tracing import current
from dispute_copilot.reliability.breaker import BreakerConfig, CircuitBreaker, load_breaker_config
from dispute_copilot.reliability.retry import RetryPolicy, load_policy

Attempt = Callable[[], tuple[int, object]]
RETRYABLE = frozenset({503})
MAX_ATTEMPTS = 3
TIMEOUT_ERROR = "timeout"
DEPENDENCIES = ("transactions", "merchant")


def _timed_out(status: int, body: object) -> bool:
    return status == 0 and isinstance(body, dict) and body.get("error") == TIMEOUT_ERROR


def read_with_retries(
    attempt: Attempt,
    max_attempts: int = MAX_ATTEMPTS,
    *,
    policy: RetryPolicy | None = None,
    dependency: str = "tool",
    breaker: CircuitBreaker | None = None,
) -> tuple[dict, int]:
    if max_attempts < 1 or max_attempts > MAX_ATTEMPTS:
        raise ValueError(f"max_attempts must be from 1 to {MAX_ATTEMPTS}")
    policy = policy or RetryPolicy(sleep=lambda _seconds: None)
    tracer = current()
    if breaker is not None and not breaker.allow():
        tracer.event("breaker.skip", level="warning", **breaker.snapshot())
        raise BreakerOpen(dependency)
    with tracer.span("tool.call", dependency=dependency, max_attempts=max_attempts) as call:
        if breaker is not None:
            call.set(breaker_state=breaker.state)
        last_status = 0
        last_body: object = None
        for n in range(1, max_attempts + 1):
            with tracer.span("tool.attempt", dependency=dependency, attempt=n) as span:
                status, body = attempt()
                timed_out = _timed_out(status, body)
                span.set(http_status=status, timed_out=timed_out)
            last_status, last_body = status, body
            if status == 200 and isinstance(body, dict):
                call.set(attempts=n, outcome="ok")
                if breaker is not None:
                    breaker.record_success()
                return body, n
            if not policy.retryable(status, timed_out):
                call.set(attempts=n, outcome="failed", http_status=status)
                raise ToolFailed(status, body, n)
            if n < max_attempts:
                wait = policy.delay(n)
                tracer.event(
                    "retry.backoff",
                    dependency=dependency,
                    attempt=n,
                    http_status=status,
                    timed_out=timed_out,
                    backoff_ms=round(wait * 1000, 1),
                )
                policy.sleep(wait)
        call.set(attempts=max_attempts, outcome="exhausted", http_status=last_status)
        tracer.event("tool.exhausted", level="warning", dependency=dependency, attempts=max_attempts)
        if breaker is not None:
            breaker.record_exhausted()
        raise ToolFailed(last_status, last_body, max_attempts)


def build_breakers(config: BreakerConfig | None = None, clock: Callable[[], float] | None = None) -> dict[str, CircuitBreaker]:
    config = config or load_breaker_config()
    if clock is None:
        return {name: CircuitBreaker(name, config) for name in DEPENDENCIES}
    return {name: CircuitBreaker(name, config, clock) for name in DEPENDENCIES}


class HttpToolClient:
    def __init__(
        self,
        http: httpx.Client,
        max_attempts: int = MAX_ATTEMPTS,
        policy: RetryPolicy | None = None,
        breakers: dict[str, CircuitBreaker] | None = None,
    ) -> None:
        if max_attempts < 1 or max_attempts > MAX_ATTEMPTS:
            raise ValueError(f"max_attempts must be from 1 to {MAX_ATTEMPTS}")
        self._http = http
        self.max_attempts = max_attempts
        self.policy = policy or load_policy()
        self.breakers = breakers if breakers is not None else build_breakers()

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
        return self._tool(case_id, "transactions")

    def merchant(self, case_id: str) -> tuple[dict, int]:
        return self._tool(case_id, "merchant")

    def _tool(self, case_id: str, name: str) -> tuple[dict, int]:
        return read_with_retries(
            lambda: self._read(f"/v1/cases/{case_id}/{name}"),
            max_attempts=self.max_attempts,
            policy=self.policy,
            dependency=name,
            breaker=self.breakers.get(name),
        )

    def _read(self, path: str) -> tuple[int, object]:
        try:
            response = self._http.get(path, timeout=self.policy.http_timeout_s)
        except httpx.TimeoutException:
            return 0, {"error": TIMEOUT_ERROR}
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
        policy: RetryPolicy | None = None,
        breakers: dict[str, CircuitBreaker] | None = None,
    ) -> None:
        if max_attempts < 1 or max_attempts > MAX_ATTEMPTS:
            raise ValueError(f"max_attempts must be from 1 to {MAX_ATTEMPTS}")
        self._cases = cases
        self._transactions = {key: list(value) for key, value in transactions.items()}
        self._merchants = {key: list(value) for key, value in merchants.items()}
        self.max_attempts = max_attempts
        self.healthy = healthy
        self.policy = policy or RetryPolicy(sleep=lambda _seconds: None)
        self.breakers = breakers or {}
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
        return read_with_retries(
            lambda: _pop(script),
            max_attempts=self.max_attempts,
            policy=self.policy,
            dependency="transactions",
            breaker=self.breakers.get("transactions"),
        )

    def merchant(self, case_id: str) -> tuple[dict, int]:
        self.merchant_calls.append(case_id)
        script = self._merchants.get(case_id, [])
        return read_with_retries(
            lambda: _pop(script),
            max_attempts=self.max_attempts,
            policy=self.policy,
            dependency="merchant",
            breaker=self.breakers.get("merchant"),
        )


def _pop(script: list[tuple[int, dict]]) -> tuple[int, dict]:
    if not script:
        return 404, {"error": "not_found"}
    return script.pop(0)
