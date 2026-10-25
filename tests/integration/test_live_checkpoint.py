import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_live_development_cases_and_the_create_boundary(tmp_path: Path):
    port = _free_port()
    state = tmp_path / "mock.sqlite"
    submissions = tmp_path / "submissions"
    server = subprocess.Popen(
        [
            sys.executable,
            "tools/runtime.py",
            "serve",
            "--data",
            "data/dev",
            "--state",
            str(state),
            "--port",
            str(port),
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_until_healthy(port, server)
        run = _copilot(port, submissions, "run-all")
        assert run.returncode == 0, run.stderr + run.stdout
        gold = json.loads((ROOT / "feedback" / "dev_gold.json").read_text(encoding="utf-8"))
        written = {path.parent.name for path in submissions.glob("drafts/*/v1.json")}
        assert written == set(gold), run.stdout + run.stderr
        rendered = "\n".join(path.read_text(encoding="utf-8") for path in submissions.rglob("*.json"))
        assert "SYNTHETIC-PRIVATE" not in rendered
        assert "CUST-" not in rendered
        outage = json.loads((submissions / "drafts" / "CARD-D07" / "v1.json").read_text(encoding="utf-8"))
        assert outage["facts"]["amount"] is None
        assert all("TRANSACTIONS" not in item["source_id"] for item in outage["citations"])
        report = tmp_path / "eval.json"
        evaluated = subprocess.run(
            [
                sys.executable,
                "tools/evaluate.py",
                "--data",
                "data/dev",
                "--gold",
                str(ROOT / "feedback" / "dev_gold.json"),
                "--submission",
                str(submissions / "results.json"),
                "--report",
                str(report),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        assert evaluated.returncode == 0, evaluated.stdout + evaluated.stderr
        summary = json.loads(report.read_text(encoding="utf-8"))
        assert summary["passed_cases"] == 12

        audit = _audit(state)
        attempts = [
            json.loads(row["detail"])["attempt"]
            for row in audit
            if row["event"] == "tool_read" and row["case_id"] == "CARD-D07" and json.loads(row["detail"])["tool"] == "transactions"
        ]
        assert attempts == [1, 2, 3]
        assert [row for row in audit if row["event"] == "record_created"] == []

        traces = tmp_path / "reports" / "traces"
        outage_trace = _trace(traces / "CARD-D07.jsonl")
        calls = [r for r in outage_trace if r["name"] == "tool.call" and r["attrs"]["dependency"] == "transactions"]
        assert [c["attrs"]["outcome"] for c in calls] == ["exhausted"]
        assert len([r for r in outage_trace if r["name"] == "retry.backoff" and r["attrs"]["dependency"] == "transactions"]) == 2
        assert not [r for r in outage_trace if r["name"] == "breaker.skip"]
        attack_trace = _trace(traces / "CARD-D08.jsonl")
        flagged = [r for r in attack_trace if r["name"] == "security.untrusted_instruction"]
        assert flagged and flagged[0]["attrs"]["action"] == "treated_as_data"
        all_trace_text = "\n".join(path.read_text(encoding="utf-8") for path in traces.glob("*.jsonl"))
        assert "SYNTHETIC-PRIVATE" not in all_trace_text
        assert "SYSTEM OVERRIDE" not in all_trace_text
        metrics = json.loads((tmp_path / "reports" / "metrics.json").read_text(encoding="utf-8"))["metrics"]
        assert metrics["cases_total"] == 12
        assert metrics["valid_contract_rate"] == 1.0
        assert metrics["breaker_opens"] == 0

        reviewed = _copilot(port, submissions, "agent-review", "CARD-D01")
        assert reviewed.returncode == 0, reviewed.stderr + reviewed.stdout
        submitted = _copilot(port, submissions, "submit-review", "CARD-D01")
        assert submitted.returncode == 0, submitted.stderr
        pending = _copilot(port, submissions, "create-record", "CARD-D01")
        assert pending.returncode == 2
        audit = _audit(state)
        assert [row for row in audit if row["event"] in {"record_created", "unapproved_write_attempt"}] == []

        handle = json.loads((submissions / "reviews" / "CARD-D01.json").read_text(encoding="utf-8"))
        approved = subprocess.run(
            [
                sys.executable,
                "tools/runtime.py",
                "approve",
                handle["review_id"],
                "--reviewer",
                "reviewer-name",
                "--state",
                str(state),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        assert approved.stdout.strip() == "approved"
        created = _copilot(port, submissions, "create-record", "CARD-D01")
        assert created.returncode == 0, created.stderr + created.stdout
        first = json.loads(created.stdout)
        again = _copilot(port, submissions, "create-record", "CARD-D01")
        assert again.returncode == 0, again.stderr
        second = json.loads(again.stdout)
        with httpx.Client(trust_env=False, timeout=5.0) as http:
            record = http.get(f"http://127.0.0.1:{port}/v1/records/CARD-D01")
        assert record.status_code == 200
        body = record.json()
        assert first["record_id"] == second["record_id"] == body["id"] == "dispute-d01"
        assert second["posted"] is False
        created_rows = [row for row in _audit(state) if row["event"] == "record_created"]
        assert len(created_rows) == 1
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()


def test_a_down_mock_writes_no_file(tmp_path: Path):
    from dispute_copilot.cli import main

    code = main(
        [
            "--mock-url",
            "http://127.0.0.1:9",
            "--data-dir",
            str(ROOT / "data" / "dev"),
            "--submissions",
            str(tmp_path),
            "run",
            "CARD-D01",
        ]
    )
    assert code == 1
    assert list(tmp_path.iterdir()) == []


def _copilot(port: int, submissions: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["LLM_PROVIDER"] = "null"
    env["REPORTS_DIR"] = str(submissions.parent / "reports")
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "dispute_copilot",
            "--mock-url",
            f"http://127.0.0.1:{port}",
            "--data-dir",
            str(ROOT / "data" / "dev"),
            "--submissions",
            str(submissions),
            *args,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        env=env,
    )


def _wait_until_healthy(port: int, server: subprocess.Popen[str]) -> None:
    deadline = time.time() + 5
    while time.time() < deadline:
        if server.poll() is not None:
            output = server.stdout.read() if server.stdout else ""
            raise AssertionError(output)
        try:
            response = httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.2, trust_env=False)
        except httpx.HTTPError:
            time.sleep(0.05)
            continue
        if response.status_code == 200:
            return
    raise AssertionError("mock did not become healthy")


def _trace(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _audit(state: Path) -> list[dict]:
    result = subprocess.run(
        [sys.executable, "tools/runtime.py", "audit", "--state", str(state)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)
