"""Confirm Postgres and Blob are reachable. Does not delete rows or write blobs."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _src_path() -> None:
    src = ROOT / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def drop_test_url() -> None:
    """POSTGRES_TEST_URL is only for disposable tests, never the live server."""
    os.environ.pop("POSTGRES_TEST_URL", None)


def check(ledger, query, blobs) -> None:
    ledger.ensure()
    row = query()
    if not isinstance(row, dict) or row.get("ok") != 1:
        raise RuntimeError("postgres")
    print("postgres ok")
    blobs("cases", "documents/")
    blobs("cases", "policies/")
    print("cases list ok")
    blobs("audit", "audit/")
    print("audit list ok")


def live_check() -> None:
    drop_test_url()
    _src_path()
    from dispute_copilot.adapters.blob_store import azure_container
    from dispute_copilot.adapters.ledger_postgres import PostgresLedger, connect_postgres

    def query():
        conn = connect_postgres()
        try:
            row = conn.execute("SELECT 1 AS ok").fetchone()
            conn.rollback()
            return row
        finally:
            conn.close()

    def blobs(kind: str, prefix: str):
        if kind == "cases":
            container = os.environ.get("CASES_CONTAINER") or "cases"
        elif kind == "audit":
            container = os.environ.get("AUDIT_CONTAINER") or "audit"
        else:
            raise RuntimeError("blob")
        azure_container(container).list(prefix)

    check(PostgresLedger.from_env(), query, blobs)


def main() -> int:
    try:
        live_check()
    except Exception as exc:
        print(type(exc).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
