"""Postgres ledger with the same operations as the SQLite file.

Imported only when LEDGER_BACKEND=postgres. psycopg is not required otherwise.
"""

import os
from contextlib import contextmanager

from dispute_copilot.fields import CASE_ID


def _driver():
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError("Install the cloud extra to use the Postgres ledger.") from exc
    return psycopg, dict_row


def connect_postgres():
    """Open the configured database. POSTGRES_TEST_URL is for disposable tests."""
    psycopg, dict_row = _driver()
    test_url = os.environ.get("POSTGRES_TEST_URL")
    if test_url:
        return psycopg.connect(test_url, row_factory=dict_row)
    host = os.environ.get("POSTGRES_HOST") or ""
    if not host:
        raise RuntimeError("POSTGRES_HOST is required when LEDGER_BACKEND=postgres.")
    return psycopg.connect(
        host=host,
        port=int(os.environ.get("POSTGRES_PORT") or "5432"),
        dbname=os.environ.get("POSTGRES_DB") or "carddisputes",
        user=os.environ.get("POSTGRES_USER") or "",
        password=os.environ.get("POSTGRES_PASSWORD") or "",
        row_factory=dict_row,
    )


def ensure_schema(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reviews (
          id TEXT PRIMARY KEY,
          case_id TEXT,
          draft TEXT,
          hash TEXT,
          status TEXT,
          reviewer TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS records (
          id TEXT PRIMARY KEY,
          case_id TEXT UNIQUE,
          draft TEXT,
          review_id TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS keys (
          key TEXT PRIMARY KEY,
          hash TEXT,
          response TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS calls (
          case_id TEXT NOT NULL,
          tool TEXT NOT NULL,
          n INTEGER NOT NULL,
          PRIMARY KEY (case_id, tool)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit (
          seq BIGSERIAL PRIMARY KEY,
          at TEXT,
          event TEXT,
          case_id TEXT,
          detail TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS review_drafts (
          case_id TEXT NOT NULL,
          version INTEGER NOT NULL,
          body JSONB NOT NULL,
          PRIMARY KEY (case_id, version)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS review_handles (
          case_id TEXT PRIMARY KEY,
          body JSONB NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS review_results (
          case_id TEXT PRIMARY KEY,
          body JSONB NOT NULL
        )
        """
    )


class PostgresSession:
    def __init__(self, conn) -> None:
        self._conn = conn
        self.events: list[dict] = []

    def insert_review(self, review_id: str, case_id: str, draft: str, draft_hash: str, status: str, reviewer: str | None) -> None:
        self._conn.execute(
            "INSERT INTO reviews (id, case_id, draft, hash, status, reviewer) VALUES (%s, %s, %s, %s, %s, %s)",
            (review_id, case_id, draft, draft_hash, status, reviewer),
        )

    def get_review(self, review_id: str) -> dict | None:
        return self._conn.execute("SELECT * FROM reviews WHERE id=%s FOR UPDATE", (review_id,)).fetchone()

    def list_pending(self) -> list[dict]:
        return list(self._conn.execute("SELECT * FROM reviews WHERE status='pending'").fetchall())

    def list_reviews(self) -> list[dict]:
        return list(self._conn.execute("SELECT * FROM reviews").fetchall())

    def set_review_status(self, review_id: str, status: str, reviewer: str) -> None:
        self._conn.execute(
            "UPDATE reviews SET status=%s, reviewer=%s WHERE id=%s",
            (status, reviewer, review_id),
        )

    def bump_call(self, case_id: str, tool: str) -> int:
        row = self._conn.execute(
            "SELECT n FROM calls WHERE case_id=%s AND tool=%s FOR UPDATE",
            (case_id, tool),
        ).fetchone()
        count = (int(row["n"]) if row else 0) + 1
        if row:
            self._conn.execute(
                "UPDATE calls SET n=%s WHERE case_id=%s AND tool=%s",
                (count, case_id, tool),
            )
        else:
            self._conn.execute(
                "INSERT INTO calls (case_id, tool, n) VALUES (%s, %s, %s)",
                (case_id, tool, count),
            )
        return count

    def get_key(self, key: str) -> dict | None:
        return self._conn.execute("SELECT * FROM keys WHERE key=%s FOR UPDATE", (key,)).fetchone()

    def insert_key(self, key: str, fingerprint: str, response: str) -> None:
        self._conn.execute(
            "INSERT INTO keys (key, hash, response) VALUES (%s, %s, %s)",
            (key, fingerprint, response),
        )

    def get_record(self, record_id: str) -> dict | None:
        return self._conn.execute("SELECT * FROM records WHERE id=%s FOR UPDATE", (record_id,)).fetchone()

    def insert_record(self, record_id: str, case_id: str, draft: str, review_id: str) -> None:
        self._conn.execute(
            "INSERT INTO records (id, case_id, draft, review_id) VALUES (%s, %s, %s, %s)",
            (record_id, case_id, draft, review_id),
        )

    def append_audit(self, at: str, event: str, case_id: str, detail: str, payload: dict) -> None:
        self._conn.execute(
            "INSERT INTO audit (at, event, case_id, detail) VALUES (%s, %s, %s, %s)",
            (at, event, case_id, detail),
        )
        self.events.append({"at": at, "event": event, CASE_ID: case_id, "detail": payload})

    def list_audit(self) -> list[dict]:
        return list(self._conn.execute("SELECT seq, at, event, case_id, detail FROM audit ORDER BY seq").fetchall())

    def reset(self) -> None:
        for table in ("reviews", "records", "keys", "calls", "audit"):
            self._conn.execute(f"DELETE FROM {table}")


class PostgresLedger:
    @classmethod
    def from_env(cls) -> "PostgresLedger":
        return cls()

    def ensure(self) -> None:
        conn = connect_postgres()
        try:
            ensure_schema(conn)
            conn.commit()
        finally:
            conn.close()

    @contextmanager
    def transaction(self, *, immediate: bool = True):
        del immediate
        conn = connect_postgres()
        session = PostgresSession(conn)
        try:
            with conn.transaction():
                yield session
        except Exception:
            raise
        else:
            from dispute_copilot.adapters.ledger import _flush_audit

            _flush_audit(session.events)
        finally:
            conn.close()
