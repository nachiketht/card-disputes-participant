import json
import random
from pathlib import Path

import httpx
import pytest

from dispute_copilot.adapters.case_system import FakeCaseSystem
from dispute_copilot.adapters.corpus import FileCorpus
from dispute_copilot.adapters.tools import FakeToolClient, HttpToolClient, read_with_retries
from dispute_copilot.errors import BreakerOpen, ToolFailed
from dispute_copilot.observability.tracing import case_trace
from dispute_copilot.reliability.breaker import (
    CLOSED,
    HALF_OPEN,
    OPEN,
    BreakerConfig,
    CircuitBreaker,
    load_breaker_config,
)
from dispute_copilot.reliability.retry import RetryPolicy, load_policy
from dispute_copilot.workflow import prepare

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "dev"
DOWN = (503, {"error": "upstream_timeout"})
TIMEOUT = (0, {"error": "timeout"})


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _policy(waits: list[float] | None = None) -> RetryPolicy:
    sink = waits if waits is not None else []
    return RetryPolicy(base_delay_s=0.1, max_delay_s=0.3, sleep=sink.append, rng=random.Random(7))


def test_backoff_is_exponential_with_capped_jitter():
    policy = RetryPolicy(base_delay_s=0.1, max_delay_s=0.3, rng=random.Random(1))
    for attempt, cap in ((1, 0.1), (2, 0.2), (3, 0.3), (4, 0.3)):
        for _ in range(50):
            wait = policy.delay(attempt)
            assert cap / 2 <= wait <= cap


def test_policy_loader_reads_config_and_rejects_more_than_three_attempts(tmp_path):
    policy = load_policy(env={})
    assert policy.max_attempts == 3
    assert policy.retry_on_timeout is True
    assert policy.max_delay_s <= 1.0
    bad = tmp_path / "retry.yaml"
    bad.write_text("max_attempts: 4\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_policy(bad, env={})
    unknown = tmp_path / "unknown.yaml"
    unknown.write_text("max_attempts: 3\nsurprise: 1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_policy(unknown, env={})


def test_env_overrides_timeout_and_backoff():
    policy = load_policy(env={"HTTP_TIMEOUT_S": "0.5", "RETRY_BASE_DELAY_S": "0", "RETRY_MAX_DELAY_S": "0"})
    assert policy.http_timeout_s == 0.5
    assert policy.delay(1) == 0


def test_backoff_waits_between_attempts_and_recovers():
    waits: list[float] = []
    script = [DOWN, TIMEOUT, (200, {"ok": True})]
    body, attempts = read_with_retries(lambda: script.pop(0), policy=_policy(waits), dependency="transactions")
    assert body == {"ok": True}
    assert attempts == 3
    assert len(waits) == 2
    assert 0.05 <= waits[0] <= 0.1 and 0.1 <= waits[1] <= 0.2


def test_a_real_http_timeout_is_retried_then_recovers():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadTimeout("simulated timeout", request=request)
        return httpx.Response(200, json={"source_id": "X", "transactions": []})

    with httpx.Client(transport=httpx.MockTransport(handler), base_url="http://mock") as http:
        client = HttpToolClient(http, policy=_policy(), breakers={})
        body, attempts = client.transactions("CARD-D10")
    assert attempts == 2
    assert body["source_id"] == "X"


def test_timeouts_on_every_attempt_fail_predictably_after_three():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectTimeout("simulated timeout", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler), base_url="http://mock") as http:
        with pytest.raises(ToolFailed) as caught:
            HttpToolClient(http, policy=_policy(), breakers={}).merchant("CARD-D07")
    assert calls["n"] == 3
    assert caught.value.attempts == 3


def test_a_404_is_not_retried():
    script = [(404, {"error": "not_found"}), (200, {})]
    with pytest.raises(ToolFailed) as caught:
        read_with_retries(lambda: script.pop(0), policy=_policy())
    assert caught.value.attempts == 1


def test_breaker_config_defaults_and_env(tmp_path):
    config = load_breaker_config(env={})
    assert config.enabled and config.failure_threshold == 3 and config.cooldown_s == 30
    override = load_breaker_config(env={"BREAKER_FAILURE_THRESHOLD": "1", "BREAKER_COOLDOWN_S": "0.5"})
    assert override.failure_threshold == 1 and override.cooldown_s == 0.5
    bad = tmp_path / "breaker.yaml"
    bad.write_text("max_attempts: 1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_breaker_config(bad, env={})


def test_breaker_never_cuts_attempts_inside_a_case():
    breaker = CircuitBreaker("transactions", BreakerConfig(failure_threshold=2, cooldown_s=10), Clock())
    for _ in range(2):
        calls = []
        with pytest.raises(ToolFailed) as caught:
            read_with_retries(lambda: calls.append(1) or DOWN, policy=_policy(), breaker=breaker)
        assert len(calls) == 3
        assert caught.value.attempts == 3
    assert breaker.state == OPEN


def test_breaker_opens_skips_probes_and_closes():
    clock = Clock()
    breaker = CircuitBreaker("transactions", BreakerConfig(failure_threshold=2, cooldown_s=10), clock)
    for _ in range(2):
        with pytest.raises(ToolFailed):
            read_with_retries(lambda: DOWN, policy=_policy(), breaker=breaker)
    assert breaker.state == OPEN
    calls = []
    with pytest.raises(BreakerOpen) as skipped:
        read_with_retries(lambda: calls.append(1) or DOWN, policy=_policy(), breaker=breaker)
    assert calls == [] and skipped.value.attempts == 0
    clock.now = 10.0
    probe = [DOWN, (200, {"ok": True})]
    body, attempts = read_with_retries(lambda: probe.pop(0), policy=_policy(), breaker=breaker)
    assert body == {"ok": True} and attempts == 2
    assert breaker.state == CLOSED and breaker.consecutive_exhausted == 0


def test_a_failed_probe_reopens_after_its_full_attempts():
    clock = Clock()
    breaker = CircuitBreaker("merchant", BreakerConfig(failure_threshold=1, cooldown_s=5), clock)
    with pytest.raises(ToolFailed):
        read_with_retries(lambda: DOWN, policy=_policy(), breaker=breaker)
    clock.now = 5.0
    assert breaker.allow() is True and breaker.state == HALF_OPEN
    calls = []
    with pytest.raises(ToolFailed) as caught:
        read_with_retries(lambda: calls.append(1) or DOWN, policy=_policy(), breaker=breaker)
    assert len(calls) == 3 and caught.value.attempts == 3
    assert breaker.state == OPEN and breaker.opens == 2


def test_a_success_between_failures_resets_the_count():
    breaker = CircuitBreaker("transactions", BreakerConfig(failure_threshold=2, cooldown_s=10), Clock())
    with pytest.raises(ToolFailed):
        read_with_retries(lambda: DOWN, policy=_policy(), breaker=breaker)
    read_with_retries(lambda: (200, {}), policy=_policy(), breaker=breaker)
    with pytest.raises(ToolFailed):
        read_with_retries(lambda: DOWN, policy=_policy(), breaker=breaker)
    assert breaker.state == CLOSED


def test_default_breaker_does_not_open_on_one_outage_case():
    breaker = CircuitBreaker("transactions", load_breaker_config(env={}), Clock())
    with pytest.raises(ToolFailed):
        read_with_retries(lambda: DOWN, policy=_policy(), breaker=breaker)
    assert breaker.state == CLOSED


def test_an_open_breaker_takes_the_existing_degradation_path(tmp_path):
    breaker = CircuitBreaker("transactions", BreakerConfig(failure_threshold=1, cooldown_s=60), Clock())
    breaker.record_exhausted()
    merchant = {"source_id": "CARD-D01-MERCHANT", "display_name": "Shop"}
    tools = FakeToolClient(
        cases=[],
        transactions={"CARD-D01": [(200, {"source_id": "CARD-D01-TRANSACTIONS", "transactions": []})]},
        merchants={"CARD-D01": [(200, merchant)]},
        breakers={"transactions": breaker},
    )
    with case_trace("CARD-D01", "run-test", tmp_path) as tracer:
        draft = prepare(
            "CARD-D01",
            corpus=FileCorpus(DATA),
            tools=tools,
            case_system=FakeCaseSystem(),
            case={"case_id": "CARD-D01", "intake_source": "CARD-D01-INTAKE", "documents": ["CARD-D01-INTAKE"]},
        )
    assert draft["routing"] == "manual_review"
    assert draft["facts"]["amount"] is None and draft["facts"]["currency"] is None
    assert "transactions tool attempts 0" in draft["narrative"]
    assert "circuit breaker was open" in draft["narrative"]
    names = [record["name"] for record in tracer.records]
    assert "breaker.skip" in names and "degradation.applied" in names
    written = [json.loads(line) for line in (tmp_path / "CARD-D01.jsonl").read_text().splitlines()]
    assert written == tracer.records


def test_retry_and_breaker_config_reject_bad_values(tmp_path: Path):
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=0)
    with pytest.raises(ValueError):
        RetryPolicy(base_delay_s=-1)
    with pytest.raises(ValueError):
        RetryPolicy(http_timeout_s=0)
    with pytest.raises(ValueError):
        BreakerConfig(failure_threshold=0)
    with pytest.raises(ValueError):
        BreakerConfig(cooldown_s=-1)
    bad = tmp_path / "breaker.yaml"
    bad.write_text("enabled: true\nnope: 1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_breaker_config(bad)
    unknown = tmp_path / "retry.yaml"
    unknown.write_text("max_attempts: 3\nunknown: 1\n", encoding="utf-8")
    from dispute_copilot.reliability.retry import load_policy as load_retry

    with pytest.raises(ValueError):
        load_retry(unknown)
    with pytest.raises(ValueError):
        read_with_retries(lambda: (200, {}), max_attempts=4)
    loaded = load_policy(tmp_path / "missing.yaml", env={})
    assert loaded.max_attempts == 3
    mapping = tmp_path / "not-a-map.yaml"
    mapping.write_text("- 1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_policy(mapping, env={})
