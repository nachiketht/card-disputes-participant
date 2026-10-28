"""Mock ledger. SQLite is the default. Postgres is selected with LEDGER_BACKEND."""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from dispute_copilot.fields import CASE_ID


def connect_sqlite(path) -> sqlite3.Connection:
    """Open the mock state file and create the five tables when they are missing."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.executescript(
        """
      CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY, case_id TEXT,
        draft TEXT, hash TEXT, status TEXT, reviewer TEXT);
      CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY, case_id TEXT UNIQUE,
        draft TEXT, review_id TEXT);
      CREATE TABLE IF NOT EXISTS keys(key TEXT PRIMARY KEY, hash TEXT, response TEXT);
      CREATE TABLE IF NOT EXISTS calls(case_id TEXT, tool TEXT, n INTEGER,
        PRIMARY KEY(case_id, tool));
      CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY AUTOINCREMENT,
        at TEXT, event TEXT, case_id TEXT, detail TEXT);
    """
    )
    return db


def _as_dict(row) -> dict | None:
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    return {key: row[key] for key in row.keys()}


class SqliteSession:
    """One transaction on the SQLite ledger file."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db
        self.events: list[dict] = []

    def insert_review(self, review_id: str, case_id: str, draft: str, draft_hash: str, status: str, reviewer: str | None) -> None:
        self._db.execute(
            "INSERT INTO reviews VALUES(?,?,?,?,?,?)",
            (review_id, case_id, draft, draft_hash, status, reviewer),
        )

    def get_review(self, review_id: str) -> dict | None:
        return _as_dict(self._db.execute("SELECT * FROM reviews WHERE id=?", (review_id,)).fetchone())

    def list_pending(self) -> list[dict]:
        rows = self._db.execute("SELECT * FROM reviews WHERE status='pending'").fetchall()
        return [_as_dict(row) for row in rows]

    def list_reviews(self) -> list[dict]:
        rows = self._db.execute("SELECT * FROM reviews").fetchall()
        return [_as_dict(row) for row in rows]

    def set_review_status(self, review_id: str, status: str, reviewer: str) -> None:
        self._db.execute(
            "UPDATE reviews SET status=?, reviewer=? WHERE id=?",
            (status, reviewer, review_id),
        )

    def bump_call(self, case_id: str, tool: str) -> int:
        row = self._db.execute("SELECT n FROM calls WHERE case_id=? AND tool=?", (case_id, tool)).fetchone()
        count = (row[0] if row else 0) + 1
        self._db.execute("INSERT OR REPLACE INTO calls VALUES(?,?,?)", (case_id, tool, count))
        return count

    def get_key(self, key: str) -> dict | None:
        return _as_dict(self._db.execute("SELECT * FROM keys WHERE key=?", (key,)).fetchone())

    def insert_key(self, key: str, fingerprint: str, response: str) -> None:
        self._db.execute("INSERT INTO keys VALUES(?,?,?)", (key, fingerprint, response))

    def get_record(self, record_id: str) -> dict | None:
        return _as_dict(self._db.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone())

    def insert_record(self, record_id: str, case_id: str, draft: str, review_id: str) -> None:
        self._db.execute("INSERT INTO records VALUES(?,?,?,?)", (record_id, case_id, draft, review_id))

    def append_audit(self, at: str, event: str, case_id: str, detail: str, payload: dict) -> None:
        self._db.execute(
            "INSERT INTO audit(at,event,case_id,detail) VALUES(?,?,?,?)",
            (at, event, case_id, detail),
        )
        self.events.append({"at": at, "event": event, CASE_ID: case_id, "detail": payload})

    def list_audit(self) -> list[dict]:
        rows = self._db.execute("SELECT * FROM audit ORDER BY seq").fetchall()
        return [_as_dict(row) for row in rows]

    def reset(self) -> None:
        for table in ("reviews", "records", "keys", "calls", "audit"):
            self._db.execute(f"DELETE FROM {table}")


class SqliteLedger:
    def __init__(self, path) -> None:
        self.path = Path(path)

    def ensure(self) -> None:
        connect_sqlite(self.path).close()

    @contextmanager
    def transaction(self, *, immediate: bool = True):
        db = connect_sqlite(self.path)
        try:
            db.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            session = SqliteSession(db)
            yield session
            db.commit()
        except Exception:
            db.rollback()
            raise
        else:
            _flush_audit(session.events)
        finally:
            db.close()


def open_ledger(path):
    """SQLite file unless LEDGER_BACKEND=postgres."""
    if os.environ.get("LEDGER_BACKEND") == "postgres":
        from dispute_copilot.adapters.ledger_postgres import PostgresLedger

        ledger = PostgresLedger.from_env()
        ledger.ensure()
        return ledger
    return SqliteLedger(path)


def _flush_audit(events: list[dict]) -> None:
    if not events or os.environ.get("AUDIT_BACKEND") != "blob":
        return
    from dispute_copilot.adapters.audit_blob import BlobAuditLog

    log = BlobAuditLog.from_env()
    for event in events:
        log.append_record(event)
