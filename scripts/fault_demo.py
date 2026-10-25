"""Fault demo against a local test double, using the real HttpToolClient.

The supplied mock answers a configured outage with a prompt 503; it cannot
hold a socket open. This script starts a small stand-in server on 127.0.0.1
that can hang past the HTTP timeout, return 503, or recover, so the real
client code shows timeout retries with backoff and the cross-case breaker.

    uv run --locked python scripts/fault_demo.py timeout
    uv run --locked python scripts/fault_demo.py breaker
"""

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dispute_copilot.adapters.tools import HttpToolClient  # noqa: E402
from dispute_copilot.errors import BreakerOpen, ToolFailed  # noqa: E402
from dispute_copilot.observability.tracing import case_trace  # noqa: E402
from dispute_copilot.reliability.breaker import BreakerConfig, CircuitBreaker  # noqa: E402
from dispute_copilot.reliability.retry import load_policy  # noqa: E402


class Script:
    """Per-request behavior: 'hang', '503' or '200'. After the list runs out, use the default."""

    def __init__(self, steps: list[str], default: str) -> None:
        self.steps = list(steps)
        self.default = default
        self.hang_s = 1.0
        self.lock = threading.Lock()
        self.served = 0

    def next(self) -> str:
        with self.lock:
            self.served += 1
            return self.steps.pop(0) if self.steps else self.default


def serve(script: Script) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            action = script.next()
            if action == "hang":
                time.sleep(script.hang_s)
            if action in {"503", "hang"}:
                body, code = {"error": "upstream_timeout", "retryable": True}, 503
            else:
                case = self.path.strip("/").split("/")[2]
                body, code = {"source_id": f"{case}-TRANSACTIONS", "transactions": []}, 200
            raw = json.dumps(body).encode()
            try:
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def show(records: list[dict]) -> None:
    for r in records:
        a = r.get("attrs", {})
        if r["name"] == "tool.attempt":
            what = "TIMEOUT" if a.get("timed_out") else f"HTTP {a.get('http_status')}"
            print(f"    attempt {a['attempt']}: {what}  ({r['duration_ms']:.0f} ms)")
        elif r["name"] == "retry.backoff":
            print(f"    backoff {a['backoff_ms']} ms before attempt {a['attempt'] + 1}")
        elif r["name"] == "tool.call":
            print(f"    -> {a['dependency']}: {a.get('outcome')} after {a.get('attempts')} attempt(s)")
        elif r["name"] == "breaker.transition":
            print(f"    BREAKER {a['from_state']} -> {a['to_state']}: {a['reason']}")
        elif r["name"] == "breaker.skip":
            print(f"    BREAKER open: call skipped, 0 attempts, case takes the degradation path")


def timeout_demo() -> int:
    script = Script(["hang", "503", "200"], "200")
    server = serve(script)
    policy = load_policy(http_timeout_s=0.3)
    print(f"HTTP_TIMEOUT_S={policy.http_timeout_s}  backoff base={policy.base_delay_s}s cap={policy.max_delay_s}s  max_attempts={policy.max_attempts}")
    print("Test double: request 1 hangs 1.0 s (past the timeout), request 2 returns 503, request 3 returns 200.")
    base = f"http://127.0.0.1:{server.server_address[1]}"
    with httpx.Client(base_url=base, trust_env=False) as http, case_trace("DEMO-TIMEOUT", "demo", None) as tracer:
        client = HttpToolClient(http, policy=policy, breakers={})
        try:
            body, attempts = client.transactions("DEMO-TIMEOUT")
            print(f"CASE DEMO-TIMEOUT: recovered on attempt {attempts}; source_id={body['source_id']}")
        except ToolFailed as exc:
            print(f"CASE DEMO-TIMEOUT: failed: {exc}")
        show(tracer.records)
    print()
    script = Script([], "hang")
    script.hang_s = 0.6
    server2 = serve(script)
    print("Test double: every request hangs past the timeout.")
    base = f"http://127.0.0.1:{server2.server_address[1]}"
    with httpx.Client(base_url=base, trust_env=False) as http, case_trace("DEMO-DOWN", "demo", None) as tracer:
        client = HttpToolClient(http, policy=policy, breakers={})
        started = time.perf_counter()
        try:
            client.transactions("DEMO-DOWN")
        except ToolFailed as exc:
            took = time.perf_counter() - started
            print(f"CASE DEMO-DOWN: fails predictably: {exc} (total {took:.2f} s, no hang)")
            print("    workflow fallback: amount and currency stay null, routing manual_review")
        show(tracer.records)
    server.shutdown()
    server2.shutdown()
    return 0


def breaker_demo() -> int:
    script = Script([], "503")
    server = serve(script)
    policy = load_policy(env={})
    config = BreakerConfig(failure_threshold=2, cooldown_s=0.5)
    print(f"Breaker: failure_threshold={config.failure_threshold} consecutive exhausted cases, cooldown_s={config.cooldown_s}")
    print("Test double: transactions returns 503 until it is healed.")
    base = f"http://127.0.0.1:{server.server_address[1]}"
    breaker = CircuitBreaker("transactions", config)
    with httpx.Client(base_url=base, trust_env=False) as http:
        client = HttpToolClient(http, policy=policy, breakers={"transactions": breaker})
        plan = [
            ("CASE-1", None),
            ("CASE-2", None),
            ("CASE-3", None),
            ("CASE-4", "wait"),
            ("CASE-5", "wait+heal"),
            ("CASE-6", None),
        ]
        for case_id, before in plan:
            if before and "wait" in before:
                time.sleep(config.cooldown_s + 0.1)
                print(f"  (waited {config.cooldown_s + 0.1:.1f} s cooldown)")
            if before and "heal" in before:
                script.default = "200"
                print("  (test double healed: transactions now returns 200)")
            served = script.served
            with case_trace(case_id, "demo", None) as tracer:
                try:
                    _body, attempts = client.transactions(case_id)
                    result = f"ok after {attempts} attempt(s)"
                except BreakerOpen:
                    result = "skipped by open breaker -> transactions_unavailable, manual_review"
                except ToolFailed as exc:
                    result = f"exhausted {exc.attempts} attempts -> transactions_unavailable, manual_review"
            print(f"{case_id}: {result}; requests sent={script.served - served}; breaker={breaker.state}")
            show(tracer.records)
    server.shutdown()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("demo", choices=["timeout", "breaker"])
    args = parser.parse_args()
    return timeout_demo() if args.demo == "timeout" else breaker_demo()


if __name__ == "__main__":
    sys.exit(main())
