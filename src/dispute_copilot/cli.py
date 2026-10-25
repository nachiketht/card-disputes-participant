"""Operator commands. Drafting, review submission, and record creation are separate.

The process exits before writing a file when the mock health check fails.
A case with no readable intake, no policy for its reported date, or a quote
that is not in its source is reported and skipped. The process does not approve
a review.
"""

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

from dispute_copilot.adapters.case_system import HttpCaseSystem
from dispute_copilot.adapters.corpus import FileCorpus
from dispute_copilot.adapters.tools import HttpToolClient
from dispute_copilot.config import Settings
from dispute_copilot.errors import CopilotError, CreateRejected, MockUnavailable, OutsideCheckpoint, ToolFailed
from dispute_copilot.fields import CASE_ID, CLASSIFICATION, DEADLINE, FACTS, POLICY_IDS, ROUTING
from dispute_copilot.llm.factory import build_critic, build_drafter
from dispute_copilot.observability import logs
from dispute_copilot.observability.metrics import read_traces, write_report
from dispute_copilot.observability.tracing import case_trace, new_run_id
from dispute_copilot.reliability.retry import load_policy
from dispute_copilot.review import create_record, submit_review
from dispute_copilot.review_flow.agent_review import format_reviewer_note, record_agent_review
from dispute_copilot.storage import latest_draft, write_draft
from dispute_copilot.workflow import prepare


def main(argv: list[str] | None = None) -> int:
    _load_env_file()
    parser = _parser()
    args = parser.parse_args(argv)
    case_id = getattr(args, "case", None)
    if args.command not in {"run-all", "metrics"} and not isinstance(case_id, str):
        print("case id is required", file=sys.stderr)
        return 1
    if not _safe_case(case_id if isinstance(case_id, str) else None):
        print("case id must be a single path segment", file=sys.stderr)
        return 1
    settings = Settings.from_sources(args.mock_url, args.data_dir, args.submissions)
    logs.configure(settings.reports_dir / "logs" / "run.jsonl", os.environ.get("LOG_STDERR", "") == "1")
    run_id = new_run_id()
    trace_dir = settings.reports_dir / "traces"
    if args.command == "metrics":
        report = write_report(settings.reports_dir / "metrics.json", read_traces(trace_dir))
        print(json.dumps({"metrics": report["metrics"], "alerts_fired": report["alerts_fired"]}, indent=2))
        return 0
    try:
        corpus = FileCorpus(settings.data_dir)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    policy = load_policy()
    with httpx.Client(base_url=settings.mock_base_url, timeout=policy.http_timeout_s, trust_env=False) as http:
        tools = HttpToolClient(http, policy=policy)
        ledger = HttpCaseSystem(http)
        try:
            tools.health()
        except MockUnavailable as exc:
            print(str(exc), file=sys.stderr)
            return 1
        try:
            drafter = build_drafter()
            if args.command == "run":
                return _run_one(case_id, settings, corpus, tools, ledger, drafter, run_id)
            if args.command == "run-all":
                return _run_all(settings, corpus, tools, ledger, drafter, run_id)
            with case_trace(case_id, run_id, trace_dir, args.command) as tracer, tracer.span(args.command):
                return _review_command(args.command, case_id, settings, ledger)
        except MockUnavailable as exc:
            print(str(exc), file=sys.stderr)
            return 1
        except FileNotFoundError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        except (OutsideCheckpoint, ToolFailed, CopilotError, CreateRejected) as exc:
            print(str(exc), file=sys.stderr)
            return 2


def _review_command(command: str, case_id: str, settings: Settings, ledger: HttpCaseSystem) -> int:
    if command == "agent-review":
        _path, draft, version = latest_draft(settings.submissions_dir, case_id)
        approval = record_agent_review(
            settings.submissions_dir,
            case_id,
            draft,
            version,
            critic=build_critic(),
            trace_dir=settings.reports_dir / "traces",
        )
        print(format_reviewer_note(draft, approval), file=sys.stderr)
        print(json.dumps(approval, indent=2))
        return 0 if approval.get("passed") is True else 2
    if command == "submit-review":
        _path, draft, _version = latest_draft(settings.submissions_dir, case_id)
        handle = submit_review(case_id, settings.submissions_dir, ledger)
        print(format_reviewer_note(draft, handle.get("agent_approval") or {}), file=sys.stderr)
        print(json.dumps(handle, indent=2))
        return 0
    outcome = create_record(case_id, settings.submissions_dir, ledger)
    print(json.dumps(outcome, indent=2))
    return 0


def _traced_case(
    case_id: str,
    settings: Settings,
    corpus,
    tools,
    ledger,
    drafter,
    run_id: str,
    case: dict | None = None,
    sink: list[dict] | None = None,
):
    """Draft and write one case inside its own trace. Trace records go to sink even when the case is skipped."""
    with case_trace(case_id, run_id, settings.reports_dir / "traces") as tracer:
        if sink is not None:
            tracer.records = sink
        with tracer.span("case", run_id=run_id) as root:
            try:
                draft = prepare(case_id, corpus=corpus, tools=tools, case_system=ledger, case=case, drafter=drafter)
            except CopilotError as exc:
                root.set(outcome="not_written", reason=type(exc).__name__)
                raise
            with tracer.span("draft.write"):
                path = write_draft(settings.submissions_dir, case_id, draft)
            root.set(
                outcome="drafted",
                contract_valid=True,
                route=draft[ROUTING],
                decision=draft[CLASSIFICATION],
                breakers=[breaker.snapshot()["state"] for breaker in getattr(tools, "breakers", {}).values()],
            )
    return draft, path


def _run_one(case_id: str, settings: Settings, corpus: FileCorpus, tools: HttpToolClient, ledger: HttpCaseSystem, drafter, run_id: str) -> int:
    draft, path = _traced_case(case_id, settings, corpus, tools, ledger, drafter, run_id)
    print(json.dumps(_summary(draft, path), indent=2))
    return 0


def run_all_report(settings: Settings, corpus: FileCorpus, tools: HttpToolClient, ledger: HttpCaseSystem, drafter, run_id: str) -> dict:
    """Draft every listed case and return the same payload the command prints."""
    written = []
    skipped = []
    records: list[dict] = []
    for case in tools.list_cases():
        case_id = case.get(CASE_ID)
        if not isinstance(case_id, str) or not _safe_case(case_id):
            skipped.append({CASE_ID: case_id, "reason": "case id is not usable"})
            continue
        traced: list[dict] = []
        try:
            draft, path = _traced_case(case_id, settings, corpus, tools, ledger, drafter, run_id, case, traced)
        except MockUnavailable:
            raise
        except (OutsideCheckpoint, ToolFailed, CopilotError) as exc:
            records.extend(traced)
            skipped.append({CASE_ID: case_id, "reason": str(exc)})
            continue
        records.extend(traced)
        written.append(_summary(draft, path))
    report = write_report(settings.reports_dir / "metrics.json", records, {"run_id": run_id})
    return {
        "written": written,
        "not_written": skipped,
        "run_id": run_id,
        "metrics": str(settings.reports_dir / "metrics.json"),
        "alerts_fired": report["alerts_fired"],
    }


def _run_all(settings: Settings, corpus: FileCorpus, tools: HttpToolClient, ledger: HttpCaseSystem, drafter, run_id: str) -> int:
    payload = run_all_report(settings, corpus, tools, ledger, drafter, run_id)
    print(json.dumps(payload, indent=2))
    return 0 if not payload["not_written"] else 2


def _summary(draft: dict, path: Path) -> dict:
    return {
        "draft": str(path),
        CASE_ID: draft[CASE_ID],
        CLASSIFICATION: draft[CLASSIFICATION],
        ROUTING: draft[ROUTING],
        POLICY_IDS: draft[POLICY_IDS],
        DEADLINE: draft[DEADLINE],
        FACTS: draft[FACTS],
    }


def _load_env_file() -> None:
    """Load .env without overriding variables already set in the process."""
    candidates = (Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env")
    seen: set[Path] = set()
    for path in candidates:
        if not path.is_file():
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key and key not in os.environ:
                os.environ[key] = value.strip().strip("'\"" )


def _safe_case(case_id: str | None) -> bool:
    if case_id is None:
        return True
    if not case_id or case_id in {".", ".."}:
        return False
    return "/" not in case_id and "\\" not in case_id


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--mock-url", default=argparse.SUPPRESS)
    common.add_argument("--data-dir", type=Path, default=argparse.SUPPRESS)
    common.add_argument("--submissions", type=Path, default=argparse.SUPPRESS)
    parser = argparse.ArgumentParser(prog="dispute-copilot")
    parser.add_argument("--mock-url", default=os.environ.get("MOCK_BASE_URL"))
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--submissions", type=Path, default=None)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", parents=[common])
    run.add_argument("case", metavar="CASE")
    commands.add_parser("run-all", parents=[common])
    review = commands.add_parser("agent-review", parents=[common])
    review.add_argument("case", metavar="CASE")
    submit = commands.add_parser("submit-review", parents=[common])
    submit.add_argument("case", metavar="CASE")
    create = commands.add_parser("create-record", parents=[common])
    create.add_argument("case", metavar="CASE")
    commands.add_parser("metrics", parents=[common])
    return parser


if __name__ == "__main__":
    sys.exit(main())
