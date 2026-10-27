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
from dispute_copilot.guardrails import loopback_host, record
from dispute_copilot.fields import CASE_ID, CLASSIFICATION, DEADLINE, FACTS, POLICY_IDS, ROUTING
from dispute_copilot.llm.factory import build_critic, build_drafter
from dispute_copilot.observability import logs
from dispute_copilot.observability.metrics import read_traces, write_report
from dispute_copilot.observability.tracing import case_trace, new_run_id
from dispute_copilot.reliability.retry import load_policy
from dispute_copilot.review import create_record, submit_review
from dispute_copilot.review_flow.agent_review import format_reviewer_note, record_agent_review
from dispute_copilot.storage import latest_draft, write_draft
from dispute_copilot.workflow import compare_approaches, prepare


def main(argv: list[str] | None = None) -> int:
    _load_env_file()
    parser = _parser()
    args = parser.parse_args(argv)
    case_id = getattr(args, "case", None)
    label = getattr(args, "label", None)
    if isinstance(label, str) and label.strip():
        os.environ["RUN_LABEL"] = label.strip()[:64]
    if args.command not in {"run-all", "metrics", "dashboard", "evals", "review-page", "compare"} and not isinstance(case_id, str):
        print("case id is required", file=sys.stderr)
        return 1
    if not _safe_case(case_id if isinstance(case_id, str) else None):
        print("case id must be a single path segment", file=sys.stderr)
        return 1
    settings = Settings.from_sources(args.mock_url, args.data_dir, args.submissions)
    logs.configure(
        settings.reports_dir / "logs" / "run.jsonl",
        os.environ.get("LOG_STDERR", "") == "1",
        os.environ.get("LOG_STDOUT", "") == "1",
    )
    run_id = new_run_id()
    trace_dir = settings.reports_dir / "traces"
    if args.command == "metrics":
        report = write_report(settings.reports_dir / "metrics.json", read_traces(trace_dir))
        print(json.dumps({"metrics": report["metrics"], "alerts_fired": report["alerts_fired"]}, indent=2))
        return 0
    if args.command == "dashboard":
        return _dashboard(args, settings)
    if args.command == "evals":
        return _evals(args, settings)
    try:
        corpus = FileCorpus(settings.data_dir)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    policy = load_policy()
    with httpx.Client(base_url=settings.mock_base_url, timeout=policy.http_timeout_s, trust_env=False) as http:
        tools = HttpToolClient(http, policy=policy)
        ledger = HttpCaseSystem(http)
        if not loopback_host(settings.mock_base_url):
            record("boundary", reason="mock_url")
            print("mock url must stay on loopback", file=sys.stderr)
            return 1
        try:
            tools.health()
        except MockUnavailable as exc:
            print(str(exc), file=sys.stderr)
            return 1
        try:
            if args.command == "review-page":
                return _serve_review_page(settings, ledger, corpus, tools, getattr(args, "state", None))
            if args.command == "compare":
                return _compare(args, settings, corpus, tools)
            drafter = build_drafter()
            if args.command == "run":
                return _run_one(case_id, settings, corpus, tools, ledger, drafter, run_id, args)
            if args.command == "run-all":
                return _run_all(settings, corpus, tools, ledger, drafter, run_id, args)
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


def _serve_review_page(
    settings: Settings,
    ledger: HttpCaseSystem,
    corpus: FileCorpus,
    tools: HttpToolClient,
    state: Path | None,
) -> int:
    import uvicorn

    root = Path(__file__).resolve().parents[2]
    if (root / "UI" / "review_page.py").is_file() and str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from UI.review_page import create_app

    if state is None:
        state = Path(".state") / "run.sqlite"
    uvicorn.run(
        create_app(
            settings.submissions_dir,
            ledger,
            corpus,
            tools,
            state_path=state,
            reports_dir=settings.reports_dir,
        ),
        host="127.0.0.1",
        port=8080,
    )
    return 0


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
    approach: str | None = None,
    shadow: bool = False,
):
    """Draft and write one case inside its own trace. Trace records go to sink even when the case is skipped."""
    with case_trace(case_id, run_id, settings.reports_dir / "traces") as tracer:
        if sink is not None:
            tracer.records = sink
        with tracer.span("case", run_id=run_id) as root:
            try:
                draft = prepare(
                    case_id,
                    corpus=corpus,
                    tools=tools,
                    case_system=ledger,
                    case=case,
                    drafter=drafter,
                    approach=approach,
                    shadow=shadow,
                    submissions_dir=settings.submissions_dir,
                    reports_dir=settings.reports_dir,
                )
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


def _run_one(case_id: str, settings: Settings, corpus: FileCorpus, tools: HttpToolClient, ledger: HttpCaseSystem, drafter, run_id: str, args) -> int:
    draft, path = _traced_case(
        case_id,
        settings,
        corpus,
        tools,
        ledger,
        drafter,
        run_id,
        approach=getattr(args, "approach", None),
        shadow=bool(getattr(args, "shadow", False)),
    )
    print(json.dumps(_summary(draft, path), indent=2))
    return 0


def _compare(args, settings: Settings, corpus: FileCorpus, tools: HttpToolClient) -> int:
    requested = [part.strip() for part in str(getattr(args, "approaches", "1,2,3")).split(",") if part.strip()]
    if not requested:
        requested = ["1", "2", "3"]
    listed = tools.list_cases()
    if isinstance(getattr(args, "case", None), str):
        chosen = [args.case]
    else:
        chosen = []
        for item in listed:
            case_id = item.get(CASE_ID) if isinstance(item, dict) else None
            if isinstance(case_id, str) and _safe_case(case_id):
                chosen.append(case_id)
    by_id = {item.get(CASE_ID): item for item in listed if isinstance(item, dict)}
    lines = ["case\tapproach\tclass\troute\tmissing\tpath"]
    for case_id in chosen:
        rows = compare_approaches(
            case_id,
            corpus=corpus,
            tools=tools,
            case=by_id.get(case_id),
            approaches=tuple(requested),
            submissions_dir=settings.submissions_dir,
            reports_dir=settings.reports_dir,
        )
        for row in rows:
            missing = ",".join(row.get("missing") or []) or "-"
            lines.append(
                "\t".join(
                    [
                        str(row.get(CASE_ID) or case_id),
                        str(row.get("approach") or ""),
                        str(row.get("final_class") or ""),
                        str(row.get("route") or ""),
                        missing,
                        str(row.get("path") or ""),
                    ]
                )
            )
    print("\n".join(lines))
    return 0


def run_all_report(
    settings: Settings,
    corpus: FileCorpus,
    tools: HttpToolClient,
    ledger: HttpCaseSystem,
    drafter,
    run_id: str,
    approach: str | None = None,
    shadow: bool = False,
) -> dict:
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
            draft, path = _traced_case(
                case_id,
                settings,
                corpus,
                tools,
                ledger,
                drafter,
                run_id,
                case,
                traced,
                approach=approach,
                shadow=shadow,
            )
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


def _run_all(settings: Settings, corpus: FileCorpus, tools: HttpToolClient, ledger: HttpCaseSystem, drafter, run_id: str, args) -> int:
    payload = run_all_report(
        settings,
        corpus,
        tools,
        ledger,
        drafter,
        run_id,
        approach=getattr(args, "approach", None),
        shadow=bool(getattr(args, "shadow", False)),
    )
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


def _evals(args, settings: Settings) -> int:
    from dispute_copilot.evals.scorecard import write_scorecard

    submission = args.submission or (settings.submissions_dir / "results.json")
    report = args.report or (settings.reports_dir / "evals.json")
    code, text = write_scorecard(
        submission=submission,
        data_dir=settings.data_dir,
        report_path=report,
        trace_dir=settings.reports_dir / "traces",
    )
    print(text)
    return code


def _dashboard(args, settings: Settings) -> int:
    from dispute_copilot.observability.board import bind_page, load_view, render, write_html

    if args.export is not None:
        write_html(settings.reports_dir, args.export)
        print(args.export)
        return 0
    port = args.port
    if not isinstance(port, int) or port < 1 or port > 65535:
        print("port must be from 1 to 65535", file=sys.stderr)
        return 1
    try:
        server = bind_page(render(load_view(settings.reports_dir)), port)
    except OSError as exc:
        print(f"could not listen on 127.0.0.1:{port}: {exc}", file=sys.stderr)
        return 1
    host, bound = server.server_address[:2]
    print(f"http://{host}:{bound}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


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
    common.add_argument("--label", default=None, help="tag traces from this run; RUN_LABEL does the same")
    parser = argparse.ArgumentParser(prog="dispute-copilot")
    parser.add_argument("--mock-url", default=os.environ.get("MOCK_BASE_URL"))
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--submissions", type=Path, default=None)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", parents=[common])
    run.add_argument("case", metavar="CASE")
    run.add_argument("--approach", choices=("1", "2", "3", "ab"), default=None)
    run.add_argument("--shadow", action="store_true")
    run_all = commands.add_parser("run-all", parents=[common])
    run_all.add_argument("--approach", choices=("1", "2", "3", "ab"), default=None)
    run_all.add_argument("--shadow", action="store_true")
    compare = commands.add_parser("compare", parents=[common])
    compare.add_argument("--case", dest="case", default=None)
    compare.add_argument("--approaches", default="1,2,3")
    page = commands.add_parser("review-page", parents=[common])
    page.add_argument("--state", type=Path, default=None, help="mock state file; default .state/run.sqlite")
    review = commands.add_parser("agent-review", parents=[common])
    review.add_argument("case", metavar="CASE")
    submit = commands.add_parser("submit-review", parents=[common])
    submit.add_argument("case", metavar="CASE")
    create = commands.add_parser("create-record", parents=[common])
    create.add_argument("case", metavar="CASE")
    commands.add_parser("metrics", parents=[common])
    evals = commands.add_parser("evals", parents=[common])
    evals.add_argument("--submission", type=Path, default=None)
    evals.add_argument("--report", type=Path, default=None)
    dashboard = commands.add_parser("dashboard", parents=[common])
    dashboard.add_argument("--export", type=Path)
    dashboard.add_argument("--port", type=int, default=8754)
    return parser


if __name__ == "__main__":
    sys.exit(main())
