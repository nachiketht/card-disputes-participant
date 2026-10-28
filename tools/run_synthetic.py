"""Run classifier approaches against the synthetic corpus. Drafter stays off.

Start a loopback mock on 8766 when one is not already healthy. Reset mock
state between approaches so fault replay stays per-run. Writes submissions and
reports under submissions/synthetic and reports/synthetic, then scores each
approach against synthetic-data/seed_gold.json.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from dispute_copilot.adapters.case_system import HttpCaseSystem
from dispute_copilot.adapters.corpus import FileCorpus
from dispute_copilot.adapters.tools import HttpToolClient
from dispute_copilot.cli import _load_env_file, _safe_case, _summary, _traced_case
from dispute_copilot.config import Settings
from dispute_copilot.errors import CopilotError, MockUnavailable, OutsideCheckpoint, ToolFailed
from dispute_copilot.fields import CASE_ID
from dispute_copilot.guardrails import loopback_host
from dispute_copilot.observability import logs
from dispute_copilot.observability.metrics import write_report
from dispute_copilot.observability.tracing import new_run_id
from dispute_copilot.reliability.retry import load_policy

DEFAULT_DATA = ROOT / "data" / "synthetic"
DEFAULT_GOLD = ROOT / "synthetic-data" / "seed_gold.json"
DEFAULT_MOCK = "http://127.0.0.1:8766"
DEFAULT_STATE = ROOT / ".state" / "synthetic.sqlite"


def parse_approaches(raw: str) -> tuple[str, ...]:
    found = []
    for part in raw.split(","):
        name = part.strip()
        if not name:
            continue
        if name not in {"1", "2", "3"}:
            raise ValueError(f"approach {name} is not 1, 2, or 3")
        if name not in found:
            found.append(name)
    if not found:
        raise ValueError("no approaches")
    return tuple(found)


def mock_healthy(url: str) -> bool:
    try:
        with urlopen(f"{url.rstrip('/')}/health", timeout=1) as response:
            return response.status == 200
    except OSError:
        return False


def mock_port(url: str) -> int:
    parsed = urlparse(url)
    if parsed.port:
        return parsed.port
    return 443 if parsed.scheme == "https" else 80


def start_mock(url: str, data: Path, state: Path) -> subprocess.Popen:
    port = mock_port(url)
    log_path = ROOT / "reports" / "synthetic" / "mock.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            sys.executable,
            "tools/runtime.py",
            "serve",
            "--data",
            str(data),
            "--state",
            str(state),
            "--port",
            str(port),
        ],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=log_file,
    )
    for _ in range(50):
        if mock_healthy(url):
            return proc
        if proc.poll() is not None:
            log_file.flush()
            raise MockUnavailable(log_path.read_text(encoding="utf-8") or "synthetic mock exited")
        time.sleep(0.2)
    proc.terminate()
    raise MockUnavailable("synthetic mock did not become healthy")


def reset_mock(state: Path) -> None:
    subprocess.run(
        [sys.executable, "tools/runtime.py", "reset", "--confirm", "--state", str(state)],
        check=True,
        cwd=ROOT,
    )


def run_one_approach(
    *,
    approach: str,
    tag: str,
    mock_url: str,
    data: Path,
) -> dict:
    submissions = ROOT / "submissions" / "synthetic" / f"a{approach}-{tag}"
    reports = ROOT / "reports" / "synthetic" / f"a{approach}-{tag}"
    reports.mkdir(parents=True, exist_ok=True)
    submissions.mkdir(parents=True, exist_ok=True)
    os.environ["REPORTS_DIR"] = str(reports)
    os.environ["RUN_LABEL"] = f"synthetic-a{approach}-{tag}"
    settings = Settings(
        mock_base_url=mock_url,
        data_dir=data,
        submissions_dir=submissions,
        reports_dir=reports,
    )
    logs.configure(settings.reports_dir / "logs" / "run.jsonl", False, False)
    corpus = FileCorpus(settings.data_dir)
    policy = load_policy()
    run_id = new_run_id()
    written: list[dict] = []
    skipped: list[dict] = []
    records: list[dict] = []
    started = time.perf_counter()
    with httpx.Client(base_url=settings.mock_base_url, timeout=policy.http_timeout_s, trust_env=False) as http:
        tools = HttpToolClient(http, policy=policy)
        ledger = HttpCaseSystem(http)
        tools.health()
        cases = tools.list_cases()
        total = len(cases)
        for index, case in enumerate(cases, start=1):
            case_id = case.get(CASE_ID)
            case_started = time.perf_counter()
            if not isinstance(case_id, str) or not _safe_case(case_id):
                skipped.append({CASE_ID: case_id, "reason": "case id is not usable"})
                print(f"a{approach} {index}/{total} skip unusable", flush=True)
                continue
            traced: list[dict] = []
            try:
                draft, path = _traced_case(
                    case_id,
                    settings,
                    corpus,
                    tools,
                    ledger,
                    None,
                    run_id,
                    case,
                    traced,
                    approach=approach,
                    shadow=False,
                )
            except MockUnavailable:
                raise
            except (OutsideCheckpoint, ToolFailed, CopilotError) as exc:
                records.extend(traced)
                skipped.append({CASE_ID: case_id, "reason": str(exc)})
                print(
                    f"a{approach} {index}/{total} {case_id} NOT_WRITTEN {exc} "
                    f"{round(time.perf_counter() - case_started, 2)}s",
                    flush=True,
                )
                continue
            records.extend(traced)
            summary = _summary(draft, path)
            written.append(summary)
            elapsed = round(time.perf_counter() - case_started, 2)
            print(
                f"a{approach} {index}/{total} {case_id} {summary.get('classification')} "
                f"{summary.get('routing')} {elapsed}s",
                flush=True,
            )
    report = write_report(settings.reports_dir / "metrics.json", records, {"run_id": run_id})
    payload = {
        "written": written,
        "not_written": skipped,
        "run_id": run_id,
        "approach": approach,
        "elapsed_s": round(time.perf_counter() - started, 3),
        "metrics": str(settings.reports_dir / "metrics.json"),
        "alerts_fired": report["alerts_fired"],
    }
    (reports / "run_all.json").write_text(json.dumps(payload, indent=2) + "\n")
    return payload


def evaluate(approach: str, tag: str, data: Path, gold: Path) -> dict:
    submission = ROOT / "submissions" / "synthetic" / f"a{approach}-{tag}" / "results.json"
    report = ROOT / "reports" / "synthetic" / f"a{approach}-{tag}" / "eval.json"
    completed = subprocess.run(
        [
            sys.executable,
            "tools/evaluate.py",
            "--data",
            str(data),
            "--gold",
            str(gold),
            "--submission",
            str(submission),
            "--report",
            str(report),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    try:
        body = json.loads(completed.stdout)
    except json.JSONDecodeError:
        body = {
            "passed": False,
            "error": completed.stderr.strip() or completed.stdout.strip() or "evaluate failed",
            "returncode": completed.returncode,
        }
    print(
        f"evaluate a{approach} passed_cases={body.get('passed_cases')} "
        f"total={body.get('total_cases')} exit={completed.returncode}",
        flush=True,
    )
    return body


def class_score(approach: str, tag: str, gold: Path) -> dict:
    answers = json.loads(gold.read_text(encoding="utf-8"))
    results = json.loads(
        (ROOT / "submissions" / "synthetic" / f"a{approach}-{tag}" / "results.json").read_text(encoding="utf-8")
    )
    by_id = {row["case_id"]: row for row in results}
    misses = []
    hits = 0
    for cid, answer in answers.items():
        pred = by_id.get(cid, {}).get("classification")
        expected = answer["classification"][0]
        if pred == expected:
            hits += 1
        else:
            misses.append(
                {
                    "case_id": cid,
                    "gold": expected,
                    "pred": pred,
                    "route": by_id.get(cid, {}).get("routing"),
                }
            )
    return {"hits": hits, "total": len(answers), "misses": misses}


def collect_classification(approaches: tuple[str, ...], tag: str) -> None:
    dest = ROOT / "reports" / "synthetic" / f"compare-{tag}" / "classification"
    dest.mkdir(parents=True, exist_ok=True)
    for approach in approaches:
        src = ROOT / "reports" / "synthetic" / f"a{approach}-{tag}" / "classification" / approach
        target = dest / approach
        target.mkdir(parents=True, exist_ok=True)
        if not src.is_dir():
            continue
        for path in src.glob("*.json"):
            (target / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approaches", default="1,2,3", help="comma-separated 1, 2, and/or 3")
    parser.add_argument("--tag", default="run", help="folder suffix, as in a2-run")
    parser.add_argument("--mock-url", default=DEFAULT_MOCK)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument(
        "--no-start-mock",
        action="store_true",
        help="fail if the mock is not already healthy",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    os.chdir(ROOT)
    _load_env_file()
    os.environ.pop("LLM_PROVIDER", None)
    os.environ["FEATURE_LLM_REVIEW"] = "off"
    args = build_parser().parse_args(argv)
    try:
        approaches = parse_approaches(args.approaches)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    mock_url = str(args.mock_url).strip()
    if not loopback_host(mock_url):
        print("mock url must stay on loopback", file=sys.stderr)
        return 1
    data = args.data if args.data.is_absolute() else ROOT / args.data
    gold = args.gold if args.gold.is_absolute() else ROOT / args.gold
    state = args.state if args.state.is_absolute() else ROOT / args.state
    if not data.is_dir():
        print(f"data dir is missing: {data}", file=sys.stderr)
        return 1
    if not gold.is_file():
        print(f"gold file is missing: {gold}", file=sys.stderr)
        return 1
    started_mock = None
    if not mock_healthy(mock_url):
        if args.no_start_mock:
            print(f"synthetic mock is not healthy at {mock_url}", file=sys.stderr)
            return 1
        print(f"starting synthetic mock at {mock_url}", flush=True)
        try:
            started_mock = start_mock(mock_url, data, state)
        except MockUnavailable as exc:
            print(str(exc), file=sys.stderr)
            return 1
    print("LLM_MODEL", os.environ.get("LLM_MODEL"), flush=True)
    print("JEV set", bool(os.environ.get("JEV_API_KEY") and os.environ.get("JEV_BASE_URL")), flush=True)
    print("CLEF set", bool(os.environ.get("CLEF_API_KEY") and os.environ.get("CLEF_ACCOUNT_ID")), flush=True)
    print("AZURE_BASE_URL set", bool(os.environ.get("AZURE_BASE_URL", "").strip()), flush=True)
    summaries = {}
    try:
        for approach in approaches:
            print(f"=== reset + approach {approach} ===", flush=True)
            reset_mock(state)
            payload = run_one_approach(approach=approach, tag=args.tag, mock_url=mock_url, data=data)
            eval_body = evaluate(approach, args.tag, data, gold)
            classed = class_score(approach, args.tag, gold)
            summaries[approach] = {
                "written": len(payload["written"]),
                "not_written": len(payload["not_written"]),
                "elapsed_s": payload["elapsed_s"],
                "run_id": payload["run_id"],
                "passed_cases": eval_body.get("passed_cases"),
                "total_cases": eval_body.get("total_cases"),
                "eval_passed": eval_body.get("passed"),
                "class_hits": classed["hits"],
                "class_total": classed["total"],
                "class_misses": classed["misses"],
            }
        collect_classification(approaches, args.tag)
        out = ROOT / "reports" / "synthetic" / f"summary-{args.tag}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summaries, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summaries, indent=2), flush=True)
        if any(row["not_written"] for row in summaries.values()):
            return 2
        return 0
    finally:
        if started_mock is not None and started_mock.poll() is None:
            started_mock.terminate()
            try:
                started_mock.wait(timeout=5)
            except subprocess.TimeoutExpired:
                started_mock.kill()


if __name__ == "__main__":
    raise SystemExit(main())
