"""Local fictional enterprise simulator. Python 3.12+, standard library only."""
import argparse
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _src_path() -> None:
    src = ROOT / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def connect(path):
    """SQLite state file. Decisions and tests keep using this connection."""
    _src_path()
    from dispute_copilot.adapters.ledger import connect_sqlite

    return connect_sqlite(path)


def _ledger(path):
    _src_path()
    from dispute_copilot.adapters.ledger import open_ledger

    return open_ledger(path)


def load_dataset(data_dir):
    if os.environ.get("CASES_BACKEND") == "blob":
        _src_path()
        from dispute_copilot.adapters.cases_blob import read_blob_dataset

        return read_blob_dataset()
    return json.loads((Path(data_dir) / "dataset.json").read_text())


def _redact_audit(text):
    """Mask restricted values in audit output. The server itself stays loopback-only."""
    _src_path()
    from dispute_copilot.evidence.redaction import Redactor

    redactor = Redactor()
    if not redactor.contains_restricted(text):
        return text
    masked = redactor.mask(text)
    if redactor.contains_restricted(masked):
        return json.dumps({"redacted": True})
    return masked


def audit(db, event, case_id="", **detail):
    at = datetime.now(timezone.utc).isoformat()
    encoded = canonical(detail)
    db.execute(
        "INSERT INTO audit(at,event,case_id,detail) VALUES(?,?,?,?)",
        (at, event, case_id, encoded),
    )
    if os.environ.get("AUDIT_BACKEND") == "blob":
        _src_path()
        from dispute_copilot.adapters.audit_blob import BlobAuditLog

        BlobAuditLog.from_env().append_record(
            {"at": at, "event": event, "case_id": case_id, "detail": detail}
        )


def _note(tx, event, case_id="", **detail):
    tx.append_audit(datetime.now(timezone.utc).isoformat(), event, case_id, canonical(detail), detail)


def create_server(data_dir, state, host="127.0.0.1", port=8765):
    dataset = load_dataset(data_dir)
    case_map = {x["case_id"]: x for x in dataset["cases"]}
    _ledger(state).ensure()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, code, body):
            raw = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            from urllib.parse import urlsplit
            path = urlsplit(self.path).path
            if path == "/health":
                return self.reply(200, {"status": "ok", "dataset": dataset["name"],
                                        "fictional_training_only": True})
            if path == "/v1/cases":
                return self.reply(200, {"cases": [{"case_id": x["case_id"],
                    "intake_source": x["intake_source"], "documents": x["documents"]}
                    for x in dataset["cases"]]})
            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts[:2] == ["v1", "reviews"]:
                with _ledger(state).transaction(immediate=False) as tx:
                    row = tx.get_review(parts[2])
                return self.reply(200, row) if row else self.reply(404, {"error": "not_found"})
            if len(parts) == 3 and parts[:2] == ["v1", "records"]:
                case = case_map.get(parts[2])
                if not case:
                    return self.reply(404, {"error": "not_found"})
                with _ledger(state).transaction(immediate=False) as tx:
                    row = tx.get_record(case["logical_key"])
                return self.reply(200, row) if row else self.reply(404, {"error": "not_found"})
            if len(parts) != 4 or parts[:2] != ["v1", "cases"]:
                return self.reply(404, {"error": "not_found"})
            cid, tool = parts[2:]
            case = case_map.get(cid)
            if not case or tool not in case["tools"]:
                return self.reply(404, {"error": "not_found"})
            payload = case["tools"][tool]
            with _ledger(state).transaction() as tx:
                count = tx.bump_call(cid, tool)
                fault = case.get("faults", {}).get(tool)
                fails = fault and (fault["failures"] == "always" or count <= fault["failures"])
                _note(tx, "tool_read", cid, tool=tool, attempt=count, status=503 if fails else 200)
            if fails:
                return self.reply(503, {"error": "upstream_timeout", "retryable": True,
                                         "message": "Simulated upstream timeout; never treat as no results."})
            return self.reply(200, payload)

        def do_POST(self):
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > 262144:
                    return self.reply(413, {"error": "body_size"})
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError()
            except (ValueError, UnicodeDecodeError):
                return self.reply(400, {"error": "invalid_json_object"})
            cid = body.get("case_id")
            if not isinstance(cid, str) or cid not in case_map:
                return self.reply(400, {"error": "unknown_case"})
            if self.path == "/v1/reviews":
                draft = body.get("draft")
                if not isinstance(draft, dict) or draft.get("case_id") != cid:
                    return self.reply(400, {"error": "draft_case_id_required"})
                rid = "rev_" + uuid.uuid4().hex[:12]
                with _ledger(state).transaction() as tx:
                    tx.insert_review(rid, cid, canonical(draft), digest(draft), "pending", None)
                    _note(tx, "review_requested", cid, review_id=rid, draft_hash=digest(draft))
                return self.reply(201, {"review_id": rid, "status": "pending", "draft_hash": digest(draft)})
            if self.path != "/v1/records":
                with _ledger(state).transaction() as tx:
                    _note(tx, "unsupported_write_attempt", cid, path=self.path)
                return self.reply(404, {"error": "unsupported_operation"})
            if not dataset["allow_write"]:
                with _ledger(state).transaction() as tx:
                    _note(tx, "forbidden_write_attempt", cid)
                return self.reply(403, {"error": "read_only_mission"})
            draft, rid = body.get("draft"), body.get("review_id")
            key = self.headers.get("Idempotency-Key", "")
            if not isinstance(draft, dict) or not isinstance(rid, str) or not key or len(key) > 128:
                return self.reply(400, {"error": "draft_review_and_idempotency_key_required"})
            if draft.get("case_id") != cid:
                return self.reply(400, {"error": "draft_case_mismatch"})
            fingerprint = digest({"case_id": cid, "draft": draft, "review_id": rid})
            with _ledger(state).transaction() as tx:
                previous = tx.get_key(key)
                if previous:
                    if previous["hash"] != fingerprint:
                        _note(tx, "idempotency_conflict", cid)
                        return self.reply(409, {"error": "idempotency_conflict"})
                    _note(tx, "write_replayed", cid)
                    return self.reply(200, json.loads(previous["response"]))
                review = tx.get_review(rid)
                if not review or review["case_id"] != cid or review["status"] != "approved" or review["hash"] != digest(draft):
                    _note(tx, "unapproved_write_attempt", cid, review_id=rid)
                    return self.reply(403, {"error": "approval_missing_or_draft_changed"})
                # logical_key represents a single dispute/claim, across duplicate intake IDs.
                logical = case_map[cid]["logical_key"]
                existing = tx.get_record(logical)
                if existing:
                    _note(tx, "duplicate_prevented", cid, existing_record_id=logical)
                    return self.reply(409, {"error": "duplicate_case", "existing_record_id": logical})
                response = {"record_id": logical, "case_id": cid, "status": "created", "review_id": rid}
                tx.insert_record(logical, cid, canonical(draft), rid)
                tx.insert_key(key, fingerprint, canonical(response))
                _note(tx, "record_created", cid, record_id=logical, review_id=rid,
                      draft_hash=digest(draft), reviewer=review["reviewer"])
            return self.reply(201, response)

    return ThreadingHTTPServer((host, port), Handler)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--data", type=Path, default=ROOT / "data" / "dev")
    serve.add_argument("--state", type=Path, default=ROOT / ".state" / "mock.sqlite")
    serve.add_argument("--port", type=int, default=8765)
    for name in ["pending", "approve", "reject", "audit", "reset"]:
        sp = sub.add_parser(name)
        sp.add_argument("--state", type=Path, default=ROOT / ".state" / "mock.sqlite")
        if name in ["approve", "reject"]:
            sp.add_argument("review_id")
            sp.add_argument("--reviewer", required=True)
        if name == "reset":
            sp.add_argument("--confirm", action="store_true", required=True)
    a = p.parse_args()
    if a.command == "serve":
        server = create_server(a.data, a.state, port=a.port)
        print(f"Fictional training mock at http://127.0.0.1:{a.port}; Ctrl-C stops it.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return
    ledger = _ledger(a.state)
    ledger.ensure()
    with ledger.transaction() as tx:
        if a.command in ["approve", "reject"]:
            status = "approved" if a.command == "approve" else "rejected"
            row = tx.get_review(a.review_id)
            if not row or row["status"] != "pending":
                sys.exit("Review must exist and be pending.")
            tx.set_review_status(a.review_id, status, a.reviewer)
            _note(tx, "human_" + status, row["case_id"], review_id=a.review_id,
                  reviewer=a.reviewer, draft_hash=row["hash"])
            print(status)
        elif a.command == "pending":
            print(json.dumps(tx.list_pending(), indent=2))
        elif a.command == "audit":
            print(_redact_audit(json.dumps(tx.list_audit(), indent=2)))
        elif a.command == "reset":
            tx.reset()
            print("Reset local mock state. Stop all clients before resetting.")


if __name__ == "__main__":
    main()
