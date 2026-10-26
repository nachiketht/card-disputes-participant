"""Local review screen for a saved draft.

The screen reads the latest draft and the review handle. It can record the
step-1 check, submit that draft, and create a record from the server copy.
It has no path that accepts a case. A person still does that with the operator
command.
"""

import os
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from dispute_copilot.adapters.case_system import FakeCaseSystem, HttpCaseSystem
from dispute_copilot.adapters.corpus import FileCorpus
from dispute_copilot.adapters.tools import FakeToolClient, HttpToolClient
from dispute_copilot.errors import CopilotError, CreateRejected, MockUnavailable
from dispute_copilot.evidence.redaction import Redactor
from dispute_copilot.fields import (
    CASE_ID,
    CITATION_CLAIM,
    CITATION_QUOTE,
    CITATION_SOURCE_ID,
    CITATIONS,
    CLASSIFICATION,
    DEADLINE,
    FACT_AMOUNT,
    FACT_CURRENCY,
    FACT_REPORTED_DATE,
    FACT_TRANSACTION_ID,
    FACTS,
    FOLLOW_UP_QUESTIONS,
    MISSING_INFORMATION,
    NARRATIVE,
    POLICY_IDS,
    QUESTION_FIELD,
    QUESTION_TEXT,
    ROUTING,
    Routing,
)
from dispute_copilot.review_flow import create_record, submit_review
from dispute_copilot.review_flow.agent_review import record_agent_review
from dispute_copilot.review_flow.handles import handle_path, read_handle
from dispute_copilot.storage import latest_draft, write_draft
from dispute_copilot.workflow import prepare

PACKAGE = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(PACKAGE / "templates"))
Ledger = HttpCaseSystem | FakeCaseSystem
_CUSTOMER_MARK = "Customer message draft:"
_REFUSED = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Draft withheld</title>
  <link rel="stylesheet" href="/static/review.css">
</head>
<body>
  <main class="withheld"><p>This draft cannot be shown.</p></main>
</body>
</html>
"""
_NOTICES = {
    "checked": "The check was saved. A pass still needs a person before a record can be created.",
    "submitted": "Submitted for review. Copy the review id into the operator command.",
    "created": "The record was created from the approved server draft.",
    "existing": "A record for this case is already in the ledger.",
    "agent_approval_required": "Submit for review was refused: agent_approval_required.",
    "approval_gate": "Create record was refused: approval_gate.",
    "refused": "The action was refused.",
}
_WARNINGS = {"agent_approval_required", "approval_gate", "refused"}


def create_app(
    submissions: Path,
    case_system: Ledger,
    corpus: FileCorpus | None = None,
    tools: HttpToolClient | FakeToolClient | None = None,
) -> FastAPI:
    """Serve the review screen for one submissions folder and one ledger client.

    When corpus and tools are passed, a case that the mock lists and that has
    no saved draft is drafted on the next page load. Existing drafts are left
    as they are.
    """
    app = FastAPI(title="Dispute review", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=str(PACKAGE / "static")), name="assets")

    def _sync() -> None:
        sync_missing_drafts(submissions, case_system, corpus, tools)

    @app.get("/", response_model=None)
    def home() -> RedirectResponse | HTMLResponse:
        _sync()
        cases = list_cases(submissions)
        if not cases:
            return _render(submissions, case_system, None, None)
        return RedirectResponse(f"/cases/{quote(str(cases[0]['id']))}", status_code=303)

    @app.get("/draft-ids", response_model=None)
    def draft_ids() -> JSONResponse:
        _sync()
        return JSONResponse({"ids": [row["id"] for row in list_cases(submissions)]})

    @app.get("/cases/{case_id}", response_model=None)
    def show_case(case_id: str, notice: str | None = None) -> HTMLResponse:
        if not _safe_case(case_id):
            return HTMLResponse("This case cannot be opened.", status_code=404)
        _sync()
        return _render(submissions, case_system, case_id, notice)

    @app.post("/cases/{case_id}/check", response_model=None)
    def check_case(case_id: str) -> HTMLResponse | RedirectResponse:
        refused = _refuse_case(case_id)
        if refused is not None:
            return refused
        try:
            _path, draft, version = latest_draft(submissions, case_id)
        except FileNotFoundError:
            return HTMLResponse("No draft has been written for this case.", status_code=404)
        critic = None
        if os.environ.get("FEATURE_LLM_REVIEW", "off").strip().lower() == "block":
            from dispute_copilot.llm.factory import build_critic

            critic = build_critic()
        record_agent_review(submissions, case_id, draft, version, critic=critic)
        return _redirect(case_id, "checked")

    @app.post("/cases/{case_id}/submit", response_model=None)
    def submit_case(case_id: str) -> HTMLResponse | RedirectResponse:
        refused = _refuse_case(case_id)
        if refused is not None:
            return refused
        try:
            submit_review(case_id, submissions, case_system)
        except FileNotFoundError:
            return HTMLResponse("No draft has been written for this case.", status_code=404)
        except CreateRejected as exc:
            return _redirect(case_id, _notice_code(exc))
        return _redirect(case_id, "submitted")

    @app.post("/cases/{case_id}/create", response_model=None)
    def create_case(case_id: str) -> HTMLResponse | RedirectResponse:
        refused = _refuse_case(case_id)
        if refused is not None:
            return refused
        try:
            outcome = create_record(case_id, submissions, case_system)
        except FileNotFoundError:
            return HTMLResponse("No draft has been written for this case.", status_code=404)
        except CreateRejected as exc:
            return _redirect(case_id, _notice_code(exc))
        if outcome.get("posted") is True:
            return _redirect(case_id, "created")
        return _redirect(case_id, "existing")

    return app


def sync_missing_drafts(
    submissions: Path,
    case_system: Ledger,
    corpus: FileCorpus | None,
    tools: HttpToolClient | FakeToolClient | None,
) -> None:
    """Write a draft for each mock case that does not have one yet."""
    if corpus is None or tools is None:
        return
    corpus.refresh()
    try:
        cases = tools.list_cases()
    except (MockUnavailable, OSError, TypeError, ValueError):
        return
    for case in cases:
        if not isinstance(case, dict):
            continue
        case_id = case.get(CASE_ID)
        if not isinstance(case_id, str) or not _safe_case(case_id) or _has_draft(submissions, case_id):
            continue
        try:
            draft = prepare(case_id, corpus=corpus, tools=tools, case_system=case_system, case=case)
        except (CopilotError, OSError, ValueError, TypeError, KeyError):
            continue
        if _has_draft(submissions, case_id):
            continue
        write_draft(submissions, case_id, draft)


def _has_draft(submissions: Path, case_id: str) -> bool:
    try:
        latest_draft(submissions, case_id)
    except FileNotFoundError:
        return False
    return True


def list_cases(submissions: Path) -> list[dict]:
    """Case ids that already have a draft, with the saved type beside each id."""
    root = submissions / "drafts"
    if not root.is_dir():
        return []
    rows = []
    for path in sorted(root.iterdir()):
        if not path.is_dir() or not _safe_case(path.name):
            continue
        try:
            _draft_path, draft, _version = latest_draft(submissions, path.name)
        except (OSError, ValueError):
            rows.append({"id": path.name, "type_value": "unreadable"})
            continue
        type_value = draft.get(CLASSIFICATION)
        rows.append({"id": path.name, "type_value": type_value if isinstance(type_value, str) else ""})
    return rows


def _render(submissions: Path, case_system: Ledger, case_id: str | None, notice: str | None) -> HTMLResponse:
    context = _context(submissions, case_system, case_id, notice)
    html = TEMPLATES.get_template("review.html").render(context)
    if Redactor().contains_restricted(html):
        return HTMLResponse(_REFUSED, status_code=403)
    return HTMLResponse(html)


def _context(submissions: Path, case_system: Ledger, case_id: str | None, notice: str | None) -> dict:
    notice_code = notice if notice in _NOTICES else ""
    base = {
        "cases": list_cases(submissions),
        "selected": case_id or "",
        "has_draft": False,
        "notice_text": _NOTICES.get(notice_code, ""),
        "notice_warn": notice_code in _WARNINGS,
        "version": "",
        "case_label": case_id or "",
        "type_value": "",
        "route_value": "",
        "route_tone": "plain",
        "policies": [],
        "fact_rows": [],
        "deadline_text": "",
        "deadline_open": False,
        "missing": [],
        "questions": [],
        "citation_rows": [],
        "narrative_text": "",
        "customer_message": "",
        "check_status": "No check yet",
        "checks": [],
        "judge_verdict": "",
        "judge_reason": "",
        "review_status": "Review not submitted",
        "review_id": "",
        "reviewer": "",
        "record_id": "",
        "empty_reason": "",
    }
    if not case_id:
        base["empty_reason"] = "Choose a draft from the list."
        return base
    try:
        _path, draft, version = latest_draft(submissions, case_id)
    except FileNotFoundError:
        base["empty_reason"] = "No draft has been written for this case."
        return base
    except (OSError, ValueError):
        base["empty_reason"] = "This draft cannot be read."
        return base
    try:
        handle = read_handle(handle_path(submissions, case_id))
    except (OSError, ValueError):
        handle = {}
        base["empty_reason"] = "The review file cannot be read."
    base.update(_draft_view(draft, version))
    base.update(_check_view(handle))
    base.update(_ledger_view(case_system, case_id, handle))
    base["has_draft"] = True
    return base


def _draft_view(draft: dict, version: int) -> dict:
    facts = draft.get(FACTS)
    if not isinstance(facts, dict):
        facts = {}
    type_value, _ignored = _show(draft.get(CLASSIFICATION))
    route_value, _ignored = _show(draft.get(ROUTING))
    deadline, deadline_open = _show(draft.get(DEADLINE))
    narrative = draft.get(NARRATIVE)
    narrative_text = narrative if isinstance(narrative, str) else ""
    return {
        "version": str(version),
        "case_label": _text(draft.get(CASE_ID)),
        "type_value": type_value,
        "route_value": route_value,
        "route_tone": _route_tone(draft.get(ROUTING)),
        "policies": _strings(draft.get(POLICY_IDS)),
        "fact_rows": [
            _fact("Transaction", facts.get(FACT_TRANSACTION_ID)),
            _fact("Amount", facts.get(FACT_AMOUNT)),
            _fact("Currency", facts.get(FACT_CURRENCY)),
            _fact("Reported date", facts.get(FACT_REPORTED_DATE)),
        ],
        "deadline_text": deadline,
        "deadline_open": deadline_open,
        "missing": [_text(item) for item in _sequence(draft.get(MISSING_INFORMATION))],
        "questions": _questions(draft.get(FOLLOW_UP_QUESTIONS)),
        "citation_rows": _citations(draft.get(CITATIONS)),
        "narrative_text": narrative_text,
        "customer_message": _customer_message(narrative_text),
    }


def _check_view(handle: dict) -> dict:
    approval = handle.get("agent_approval")
    if not isinstance(approval, dict):
        return {"check_status": "No check yet", "checks": [], "judge_verdict": "", "judge_reason": ""}
    checks = []
    raw = approval.get("checks")
    if isinstance(raw, dict):
        for name, passed in raw.items():
            checks.append({"label": str(name).replace("_", " "), "passed": passed is True})
    verdict = ""
    reason = ""
    judge = approval.get("llm_review")
    if isinstance(judge, dict):
        if isinstance(judge.get("verdict"), str):
            verdict = judge["verdict"]
        if isinstance(judge.get("reason"), str):
            reason = judge["reason"]
    status = "Check passed" if approval.get("passed") is True else "Check blocked"
    return {"check_status": status, "checks": checks, "judge_verdict": verdict, "judge_reason": reason}


def _ledger_view(case_system: Ledger, case_id: str, handle: dict) -> dict:
    review_id = handle.get("review_id")
    if not isinstance(review_id, str):
        review_id = ""
    status = "Review not submitted"
    reviewer = ""
    if review_id:
        status, reviewer = _remote_review(case_system, review_id)
    found, record_id = _remote_record(case_system, case_id)
    if found:
        status = "Record created"
    return {"review_status": status, "review_id": review_id, "reviewer": reviewer, "record_id": record_id}


def _remote_review(case_system: Ledger, review_id: str) -> tuple[str, str]:
    try:
        review = case_system.get_review(review_id)
    except (CreateRejected, MockUnavailable, OSError):
        return "Review pending", ""
    reviewer = review.reviewer if isinstance(review.reviewer, str) else ""
    if review.status == "approved":
        return "Review approved", reviewer
    if review.status == "rejected":
        return "Review rejected", reviewer
    return "Review pending", reviewer


def _remote_record(case_system: Ledger, case_id: str) -> tuple[bool, str]:
    try:
        found = case_system.get_record(case_id)
    except (CreateRejected, MockUnavailable, OSError):
        return False, ""
    if found.found and isinstance(found.record_id, str):
        return True, found.record_id
    return False, ""


def _fact(label: str, value: object) -> dict:
    shown, is_null = _show(value)
    return {"label": label, "value": shown, "is_null": is_null}


def _show(value: object) -> tuple[str, bool]:
    if value is None:
        return "null", True
    return str(value), False


def _text(value: object) -> str:
    if value is None:
        return "null"
    return str(value)


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _sequence(value: object) -> list:
    if not isinstance(value, list):
        return []
    return value


def _questions(value: object) -> list[dict]:
    rows = []
    for item in _sequence(value):
        if not isinstance(item, dict):
            continue
        rows.append({"field_id": _text(item.get(QUESTION_FIELD)), "prompt": _text(item.get(QUESTION_TEXT))})
    return rows


def _citations(value: object) -> list[dict]:
    rows = []
    for item in _sequence(value):
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "claim_text": _text(item.get(CITATION_CLAIM)),
                "source": _text(item.get(CITATION_SOURCE_ID)),
                "excerpt": _text(item.get(CITATION_QUOTE)),
            }
        )
    return rows


def _customer_message(narrative: str) -> str:
    _head, sep, tail = narrative.partition(_CUSTOMER_MARK)
    if not sep:
        return ""
    return tail.strip()


def _route_tone(value: object) -> str:
    if value == Routing.READY_FOR_REVIEW:
        return "ready"
    if value == Routing.REQUEST_INFORMATION:
        return "ask"
    if value == Routing.MANUAL_REVIEW:
        return "hold"
    return "plain"


def _safe_case(case_id: str) -> bool:
    if not case_id or case_id in {".", ".."} or ".." in case_id:
        return False
    return "/" not in case_id and "\\" not in case_id


def _refuse_case(case_id: str) -> HTMLResponse | None:
    if _safe_case(case_id):
        return None
    return HTMLResponse("This case cannot be opened.", status_code=404)


def _notice_code(exc: CreateRejected) -> str:
    payload = exc.payload
    if isinstance(payload, dict):
        error = payload.get("error")
        if error in _NOTICES:
            return str(error)
    return "refused"


def _redirect(case_id: str, notice: str) -> RedirectResponse:
    return RedirectResponse(url=f"/cases/{quote(case_id)}?notice={quote(notice)}", status_code=303)
