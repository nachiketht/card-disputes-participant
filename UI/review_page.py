"""Local review screen for a saved draft.

The screen reads the latest draft and the review handle. It can record the
step-1 check, submit that draft, and create a record from the server copy.
A person accepts or declines a pending review on the decisions page. That
page writes the mock state file and leaves the stored draft hash unchanged.
"""

import json
import os
import sqlite3
from html import escape
from pathlib import Path
from urllib.parse import parse_qs, quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from dispute_copilot.adapters.case_system import FakeCaseSystem, HttpCaseSystem
from dispute_copilot.adapters.cases_blob import BlobCorpus
from dispute_copilot.adapters.corpus import FileCorpus, open_corpus
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
    Classification,
    Routing,
)
from dispute_copilot.rules.documents import complaint_text
from dispute_copilot.review_flow import create_record, submit_review
from dispute_copilot.llm.base import LLMError
from dispute_copilot.review_flow.agent_review import record_agent_review
from dispute_copilot.review_flow.handles import handle_path, read_handle
from dispute_copilot.storage import latest_draft, write_draft
from dispute_copilot.workflow import prepare

from dispute_copilot.adapters.ledger import open_ledger

from UI.decisions import DecisionRefused, list_reviews, record_decision

Corpus = FileCorpus | BlobCorpus

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
    "submitted": "Submitted for review. Accept or decline it on the decisions page.",
    "created": "The record was created from the approved server draft.",
    "existing": "A record for this case is already in the ledger.",
    "agent_approval_required": "Submit for review was refused: agent_approval_required.",
    "approval_gate": "Create record was refused: approval_gate.",
    "refused": "The action was refused.",
    "feedback": "Feedback was saved. It does not accept or decline the review.",
    "feedback_name": "A reviewer name is required.",
    "feedback_category": "Choose the correct category.",
    "judge_url": (
        "The judge step was skipped because AZURE_BASE_URL is empty. "
        "The checklist was saved. This note does not accept the case."
    ),
    "judge_key": (
        "The judge step was skipped because LLM_API_KEY is empty. "
        "The checklist was saved. This note does not accept the case."
    ),
    "judge_model": (
        "The judge step was skipped because the model call failed. "
        "The checklist was saved. This note does not accept the case."
    ),
    "judge_client": (
        "The judge step was skipped because no model client is configured. "
        "The checklist was saved. This note does not accept the case."
    ),
}
_WARNINGS = {"agent_approval_required", "approval_gate", "refused", "feedback_name", "feedback_category"}
_DECISION_NOTICES = {
    "accepted": "Accepted. Create the record on this page when you are ready.",
    "declined": "Declined. A record will not be created from this review.",
    "name": "A reviewer name is required.",
    "pending": "That review is not pending.",
    "unavailable": "The mock state file could not be updated.",
    "created": "The record was created from the approved server draft.",
    "existing": "A record for this case is already in the ledger.",
    "approval_gate": "Create record was refused: approval_gate.",
    "refused": "The action was refused.",
}
_DECISION_WARNINGS = {"name", "pending", "unavailable", "approval_gate", "refused"}


def create_app(
    submissions: Path,
    case_system: Ledger,
    corpus: Corpus | None = None,
    tools: HttpToolClient | FakeToolClient | None = None,
    state_path: Path | None = None,
    reports_dir: Path | None = None,
) -> FastAPI:
    """Serve the review screen for one submissions folder and one ledger client.

    When corpus and tools are passed, a case that the mock lists and that has
    no saved draft is drafted on the next page load. Existing drafts are left
    as they are.
    """
    if corpus is None and os.environ.get("CASES_BACKEND") == "blob":
        from dispute_copilot.config import Settings

        corpus = open_corpus(Settings.from_sources(None, None, None).data_dir)
    app = FastAPI(title="Dispute review", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=str(PACKAGE / "static")), name="assets")

    def _sync() -> None:
        sync_missing_drafts(submissions, case_system, corpus, tools)

    @app.get("/", response_model=None)
    def home() -> RedirectResponse | HTMLResponse:
        _sync()
        cases = list_cases(submissions, case_system, state_path)
        if not cases:
            return _render(submissions, case_system, None, None, corpus, tools, state_path)
        return RedirectResponse(f"/cases/{quote(str(cases[0]['id']))}", status_code=303)

    @app.get("/draft-ids", response_model=None)
    def draft_ids() -> JSONResponse:
        _sync()
        return JSONResponse({"ids": [row["id"] for row in list_cases(submissions, case_system, state_path)]})

    @app.get("/cases/{case_id}", response_model=None)
    def show_case(case_id: str, notice: str | None = None) -> HTMLResponse:
        if not _safe_case(case_id):
            return HTMLResponse("This case cannot be opened.", status_code=404)
        _sync()
        return _render(submissions, case_system, case_id, notice, corpus, tools, state_path)

    @app.get("/cases/{case_id}/model-note", response_model=None)
    def model_note(case_id: str) -> JSONResponse:
        if not _safe_case(case_id):
            return JSONResponse({"error": "not_found"}, status_code=404)
        return JSONResponse(_model_note(submissions, case_id))

    @app.post("/cases/{case_id}/feedback", response_model=None)
    async def save_feedback(case_id: str, request: Request) -> HTMLResponse | RedirectResponse:
        refused = _refuse_case(case_id)
        if refused is not None:
            return refused
        raw = await request.body()
        parsed = parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True)
        notice = _store_feedback(submissions, reports_dir, case_id, parsed)
        return _redirect(case_id, notice)

    @app.post("/cases/{case_id}/check", response_model=None)
    def check_case(case_id: str) -> HTMLResponse | RedirectResponse:
        refused = _refuse_case(case_id)
        if refused is not None:
            return refused
        closed = _closed_redirect(submissions, case_system, state_path, case_id)
        if closed is not None:
            return closed
        try:
            _path, draft, version = latest_draft(submissions, case_id)
        except FileNotFoundError:
            return HTMLResponse("No draft has been written for this case.", status_code=404)
        critic = None
        judge_skip = None
        if os.environ.get("FEATURE_LLM_REVIEW", "off").strip().lower() == "block":
            critic, judge_skip = _judge_for_check()
        approval = record_agent_review(
            submissions,
            case_id,
            draft,
            version,
            critic=critic,
            judge_skip=judge_skip,
        )
        return _redirect(case_id, _check_notice(approval, judge_skip))

    @app.post("/cases/{case_id}/submit", response_model=None)
    def submit_case(case_id: str) -> HTMLResponse | RedirectResponse:
        refused = _refuse_case(case_id)
        if refused is not None:
            return refused
        closed = _closed_redirect(submissions, case_system, state_path, case_id)
        if closed is not None:
            return closed
        try:
            submit_review(case_id, submissions, case_system)
        except FileNotFoundError:
            return HTMLResponse("No draft has been written for this case.", status_code=404)
        except CreateRejected as exc:
            return _redirect(case_id, _notice_code(exc))
        return _redirect(case_id, "submitted")

    @app.post("/cases/{case_id}/create", response_model=None)
    def create_case(case_id: str) -> HTMLResponse | RedirectResponse:
        return _create_case(submissions, case_system, state_path, case_id, on_decisions=False)

    @app.post("/decisions/{case_id}/record", response_model=None)
    def create_from_decisions(case_id: str) -> HTMLResponse | RedirectResponse:
        return _create_case(submissions, case_system, state_path, case_id, on_decisions=True)

    @app.get("/decisions", response_model=None)
    def show_decisions(notice: str | None = None, case: str | None = None) -> HTMLResponse:
        _sync()
        return _render_decisions(submissions, case_system, state_path, notice, case)

    @app.post("/decisions/{review_id}/accept", response_model=None)
    async def accept_review(review_id: str, request: Request) -> RedirectResponse:
        return await _decide(state_path, review_id, request, accepted=True)

    @app.post("/decisions/{review_id}/decline", response_model=None)
    async def decline_review(review_id: str, request: Request) -> RedirectResponse:
        return await _decide(state_path, review_id, request, accepted=False)

    return app


def _create_case(
    submissions: Path,
    case_system: Ledger,
    state_path: Path | None,
    case_id: str,
    *,
    on_decisions: bool,
) -> HTMLResponse | RedirectResponse:
    refused = _refuse_case(case_id)
    if refused is not None:
        return refused
    already = case_outcome(submissions, case_system, state_path, case_id)
    if already:
        return _decision_redirect(already, case_id)
    try:
        outcome = create_record(case_id, submissions, case_system)
    except FileNotFoundError:
        return HTMLResponse("No draft has been written for this case.", status_code=404)
    except CreateRejected as exc:
        notice = _notice_code(exc)
        if notice in {"created", "existing"}:
            return _decision_redirect(notice, case_id)
        if on_decisions:
            return _decision_redirect(notice, case_id)
        return _redirect(case_id, notice)
    notice = "created" if outcome.get("posted") is True else "existing"
    return _decision_redirect(notice, case_id)


async def _decide(state_path: Path | None, review_id: str, request: Request, *, accepted: bool) -> RedirectResponse:
    if state_path is None:
        return RedirectResponse("/decisions?notice=unavailable", status_code=303)
    name = await _posted_name(request)
    try:
        status = record_decision(state_path, review_id, name, accepted=accepted)
    except (DecisionRefused, OSError, sqlite3.Error) as exc:
        code = exc.code if isinstance(exc, DecisionRefused) else "unavailable"
        return RedirectResponse(f"/decisions?notice={quote(code)}", status_code=303)
    notice = "accepted" if status == "approved" else "declined"
    return _decision_redirect(notice, _case_id_for_review(state_path, review_id))


async def _posted_name(request: Request) -> str:
    raw = await request.body()
    parsed = parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True)
    values = parsed.get("reviewer") or [""]
    return values[0] if values else ""


def _render_decisions(
    submissions: Path,
    case_system: Ledger,
    state_path: Path | None,
    notice: str | None,
    case_id: str | None,
) -> HTMLResponse:
    code = notice if notice in _DECISION_NOTICES else ""
    try:
        reviews = _with_outcomes(list_reviews(state_path), case_system)
    except (OSError, sqlite3.Error, DecisionRefused):
        reviews = []
        code = "unavailable"
    html = TEMPLATES.get_template("decisions.html").render(
        {
            "cases": list_cases(submissions, case_system, state_path),
            "reviews": reviews,
            "state_ready": state_path is not None,
            "notice_text": _decision_notice(code, case_id),
            "notice_warn": code in _DECISION_WARNINGS,
        }
    )
    if Redactor().contains_restricted(html):
        return HTMLResponse(_REFUSED, status_code=403)
    return HTMLResponse(html)


def sync_missing_drafts(
    submissions: Path,
    case_system: Ledger,
    corpus: Corpus | None,
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


def list_cases(submissions: Path, case_system: Ledger | None = None, state_path: Path | None = None) -> list[dict]:
    """Open drafts only. A created or declined case stays on disk and leaves this list."""
    if os.environ.get("REVIEW_BACKEND") == "postgres":
        from dispute_copilot.adapters.review_state import PostgresReviewStore

        return _list_case_ids(
            PostgresReviewStore.from_env().draft_case_ids(),
            submissions,
            case_system,
            state_path,
        )
    root = submissions / "drafts"
    if not root.is_dir():
        return []
    rows = []
    for path in sorted(root.iterdir()):
        if not path.is_dir() or not _safe_case(path.name):
            continue
        if case_outcome(submissions, case_system, state_path, path.name):
            continue
        try:
            _draft_path, draft, _version = latest_draft(submissions, path.name)
        except (OSError, ValueError):
            rows.append({"id": path.name, "type_value": "unreadable"})
            continue
        type_value = draft.get(CLASSIFICATION)
        rows.append({"id": path.name, "type_value": type_value if isinstance(type_value, str) else ""})
    return rows


def _list_case_ids(case_ids: list[str], submissions: Path, case_system: Ledger | None, state_path: Path | None) -> list[dict]:
    rows = []
    for case_id in case_ids:
        if not _safe_case(case_id):
            continue
        if case_outcome(submissions, case_system, state_path, case_id):
            continue
        try:
            _draft_path, draft, _version = latest_draft(submissions, case_id)
        except (OSError, ValueError):
            rows.append({"id": case_id, "type_value": "unreadable"})
            continue
        type_value = draft.get(CLASSIFICATION)
        rows.append({"id": case_id, "type_value": type_value if isinstance(type_value, str) else ""})
    return rows


def case_outcome(
    submissions: Path,
    case_system: Ledger | None,
    state_path: Path | None,
    case_id: str,
) -> str:
    """`created` or `declined` when the case is finished. Empty while it is still a draft."""
    if case_system is not None and _remote_record(case_system, case_id)[0]:
        return "created"
    if _review_status(submissions, case_system, case_id) == "rejected":
        return "declined"
    if _sqlite_status(state_path, case_id) == "rejected":
        return "declined"
    return ""


def _render(
    submissions: Path,
    case_system: Ledger,
    case_id: str | None,
    notice: str | None,
    corpus: Corpus | None = None,
    tools: HttpToolClient | FakeToolClient | None = None,
    state_path: Path | None = None,
) -> HTMLResponse:
    context = _context(submissions, case_system, case_id, notice, state_path)
    complaint = _complaint_text(corpus, tools, case_id)
    slot = "<!-- complaint-slot -->"
    context["complaint_slot"] = slot
    context["has_complaint"] = bool(complaint.strip())
    context["categories"] = [item.value for item in Classification]
    context["model_note"] = _model_note(submissions, case_id) if case_id else _empty_note()
    html = TEMPLATES.get_template("review.html").render(context)
    page = html.replace(slot, escape(complaint), 1)
    if Redactor().contains_restricted(page):
        return HTMLResponse(_REFUSED, status_code=403)
    return HTMLResponse(page)


def _context(
    submissions: Path,
    case_system: Ledger,
    case_id: str | None,
    notice: str | None,
    state_path: Path | None = None,
) -> dict:
    notice_code = notice if notice in _NOTICES else ""
    base = {
        "cases": list_cases(submissions, case_system, state_path),
        "decided": False,
        "confirmation": "",
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
        "check_tone": "",
        "checks": [],
        "judge_verdict": "",
        "judge_reason": "",
        "review_status": "Review not submitted",
        "review_tone": "",
        "review_id": "",
        "reviewer": "",
        "record_id": "",
        "empty_reason": "",
    }
    if not case_id:
        base["empty_reason"] = "No open drafts. Created and declined cases are under Decisions."
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
    outcome = case_outcome(submissions, case_system, state_path, case_id)
    if outcome == "created":
        base["decided"] = True
        base["confirmation"] = f"{case_id} was created."
    elif outcome == "declined":
        base["decided"] = True
        base["confirmation"] = f"{case_id} was declined."
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


def _status_tone(status: str) -> str:
    if status in {"Check passed", "Review approved", "Record created"}:
        return "ready"
    if status in {"Check blocked", "Review rejected"}:
        return "hold"
    if status == "Review pending":
        return "ask"
    return ""


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
    return {
        "check_status": status,
        "check_tone": _status_tone(status),
        "checks": checks,
        "judge_verdict": verdict,
        "judge_reason": reason,
    }


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
    return {
        "review_status": status,
        "review_tone": _status_tone(status),
        "review_id": review_id,
        "reviewer": reviewer,
        "record_id": record_id,
    }


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


def _judge_for_check() -> tuple[object | None, str | None]:
    """A critic when the judge can run. Otherwise a skip sentence and no client."""
    from dispute_copilot.llm.factory import build_critic
    from dispute_copilot.llm.openai_compat import resolve_api_key

    if not resolve_api_key():
        return None, _NOTICES["judge_key"]
    if not os.environ.get("AZURE_BASE_URL", "").strip():
        return None, _NOTICES["judge_url"]
    try:
        critic = build_critic()
    except LLMError:
        return None, _NOTICES["judge_model"]
    if critic is None:
        return None, _NOTICES["judge_client"]
    return critic, None


def _check_notice(approval: dict, judge_skip: str | None) -> str:
    review = approval.get("llm_review")
    reason = judge_skip or ""
    if isinstance(review, dict) and review.get("mode") == "skipped":
        reason = str(review.get("reason") or reason)
    if not reason:
        return "checked"
    if "AZURE_BASE_URL" in reason:
        return "judge_url"
    if "LLM_API_KEY" in reason:
        return "judge_key"
    if "model call failed" in reason:
        return "judge_model"
    return "judge_client"


def _notice_code(exc: CreateRejected) -> str:
    payload = exc.payload
    if isinstance(payload, dict):
        error = payload.get("error")
        if error in _NOTICES:
            return str(error)
    return "refused"


def _redirect(case_id: str, notice: str) -> RedirectResponse:
    return RedirectResponse(url=f"/cases/{quote(case_id)}?notice={quote(notice)}", status_code=303)


def _decision_redirect(notice: str, case_id: str) -> RedirectResponse:
    target = f"/decisions?notice={quote(notice)}"
    if case_id and _safe_case(case_id):
        target += f"&case={quote(case_id)}"
    return RedirectResponse(target, status_code=303)


def _closed_redirect(
    submissions: Path,
    case_system: Ledger,
    state_path: Path | None,
    case_id: str,
) -> RedirectResponse | None:
    outcome = case_outcome(submissions, case_system, state_path, case_id)
    if not outcome:
        return None
    return _decision_redirect(outcome, case_id)


def _decision_notice(code: str, case_id: str | None) -> str:
    label = case_id if isinstance(case_id, str) and _safe_case(case_id) else ""
    if label and code in {"created", "existing"}:
        return f"{label} was created."
    if label and code == "declined":
        return f"{label} was declined."
    return _DECISION_NOTICES.get(code, "")


def _case_id_for_review(state_path: Path | None, review_id: str) -> str:
    if os.environ.get("LEDGER_BACKEND") != "postgres" and (state_path is None or not Path(state_path).is_file()):
        return ""
    try:
        with open_ledger(state_path).transaction(immediate=False) as tx:
            row = tx.get_review(review_id)
    except (OSError, sqlite3.Error, DecisionRefused):
        return ""
    if row is None or not isinstance(row["case_id"], str):
        return ""
    return row["case_id"]


def _review_status(submissions: Path, case_system: Ledger | None, case_id: str) -> str:
    if case_system is None:
        return ""
    try:
        handle = read_handle(handle_path(submissions, case_id))
    except (OSError, ValueError):
        return ""
    review_id = handle.get("review_id")
    if not isinstance(review_id, str) or not review_id:
        return ""
    try:
        review = case_system.get_review(review_id)
    except (CreateRejected, MockUnavailable, OSError):
        return ""
    return review.status if isinstance(review.status, str) else ""


def _sqlite_status(state_path: Path | None, case_id: str) -> str:
    if os.environ.get("LEDGER_BACKEND") != "postgres" and (state_path is None or not Path(state_path).is_file()):
        return ""
    try:
        with open_ledger(state_path).transaction(immediate=False) as tx:
            rows = [row for row in tx.list_reviews() if row.get("case_id") == case_id]
    except (OSError, sqlite3.Error, DecisionRefused):
        return ""
    statuses = [row["status"] for row in rows]
    if statuses and all(status == "rejected" for status in statuses):
        return "rejected"
    return ""


def _with_outcomes(reviews: list[dict], case_system: Ledger) -> list[dict]:
    listed = []
    for item in reviews:
        row = dict(item)
        row["created"] = False
        row["record_id"] = ""
        case_id = row.get("case_id")
        if isinstance(case_id, str):
            found, record_id = _remote_record(case_system, case_id)
            if found:
                row["created"] = True
                row["record_id"] = record_id
                row["status_label"] = "Created"
                row["tone"] = "ready"
                row["pending"] = False
        listed.append(row)
    return listed


def _complaint_text(
    corpus: Corpus | None,
    tools: HttpToolClient | FakeToolClient | None,
    case_id: str | None,
) -> str:
    """Customer complaint for the open case. Empty when the intake is not loaded."""
    if corpus is None or tools is None or not case_id:
        return ""
    try:
        cases = tools.list_cases()
    except (MockUnavailable, OSError, TypeError, ValueError):
        return ""
    intake_id = ""
    for case in cases:
        if isinstance(case, dict) and case.get(CASE_ID) == case_id:
            source = case.get("intake_source")
            intake_id = source if isinstance(source, str) else ""
            break
    if not intake_id:
        return ""
    try:
        return complaint_text(corpus.get(intake_id))
    except (OSError, ValueError, KeyError):
        return ""


def _empty_note() -> dict:
    return {
        "haiku_called": False,
        "haiku_class": None,
        "haiku_reason": "",
        "haiku_unusable": False,
        "haiku_quote_missing": False,
    }


def _model_note(submissions: Path, case_id: str) -> dict:
    empty = _empty_note()
    path = submissions / CLASSIFICATION / "applied" / f"{case_id}.json"
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return empty
    if not isinstance(loaded, dict):
        return empty
    reason = loaded.get("haiku_reason")
    chosen = loaded.get("haiku_class")
    return {
        "haiku_called": loaded.get("haiku_called") is True,
        "haiku_class": chosen if isinstance(chosen, str) else None,
        "haiku_reason": reason if isinstance(reason, str) else "",
        "haiku_unusable": loaded.get("haiku_unusable") is True,
        "haiku_quote_missing": loaded.get("haiku_quote_missing") is True,
    }


def _store_feedback(submissions: Path, reports_dir: Path | None, case_id: str, parsed: dict) -> str:
    name = _posted(parsed, "reviewer")
    category = _posted(parsed, "category")
    note = _posted(parsed, "note")
    opened = _posted(parsed, "haiku_opened").strip().lower() in {"1", "true", "on", "yes"}
    if not name.strip():
        return "feedback_name"
    allowed = {item.value for item in Classification}
    if category not in allowed:
        return "feedback_category"
    body = {
        CASE_ID: case_id,
        "reviewer": name.strip(),
        "category": category,
        "note": note.strip(),
        "haiku_block_opened": opened,
    }
    text = json.dumps(body, indent=2, sort_keys=True) + "\n"
    folders = [submissions]
    if reports_dir is not None:
        folders.append(reports_dir)
    for folder in folders:
        path = folder / "feedback" / f"{case_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return "feedback"


def _posted(parsed: dict, key: str) -> str:
    values = parsed.get(key) or [""]
    return values[0] if values else ""
