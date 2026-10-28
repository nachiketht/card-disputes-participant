"""Review drafts, handles, and results. Files stay the default.

Postgres is used only when REVIEW_BACKEND=postgres. It shares the ledger database.
"""

import os

from dispute_copilot.adapters.ledger_postgres import connect_postgres, ensure_schema
from dispute_copilot.fields import CASE_ID, CLASSIFICATION


def postgres_review_store():
    """The Postgres store when that backend is selected, otherwise None."""
    if os.environ.get("REVIEW_BACKEND") != "postgres":
        return None
    return PostgresReviewStore.from_env()


class PostgresReviewStore:
    @classmethod
    def from_env(cls) -> "PostgresReviewStore":
        return cls()

    def _conn(self):
        conn = connect_postgres()
        ensure_schema(conn)
        conn.commit()
        return conn

    def write_draft(self, case_id: str, draft: dict) -> int:
        from psycopg.types.json import Json

        conn = self._conn()
        try:
            with conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (case_id,))
                current = conn.execute(
                    "SELECT COALESCE(MAX(version), 0) AS version FROM review_drafts WHERE case_id=%s",
                    (case_id,),
                ).fetchone()
                version = int(current["version"]) + 1
                conn.execute(
                    "INSERT INTO review_drafts (case_id, version, body) VALUES (%s, %s, %s)",
                    (case_id, version, Json(draft)),
                )
                conn.execute(
                    """
                    INSERT INTO review_results (case_id, body) VALUES (%s, %s)
                    ON CONFLICT (case_id) DO UPDATE SET body = EXCLUDED.body
                    """,
                    (case_id, Json(draft)),
                )
            return version
        finally:
            conn.close()

    def latest(self, case_id: str) -> tuple[dict, int]:
        conn = self._conn()
        try:
            row = conn.execute(
                """
                SELECT version, body FROM review_drafts
                WHERE case_id=%s ORDER BY version DESC LIMIT 1
                """,
                (case_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise FileNotFoundError(f"no draft has been written for {case_id}")
        body = row["body"]
        if isinstance(body, str):
            import json

            body = json.loads(body)
        if not isinstance(body, dict):
            raise ValueError("draft is not an object")
        return body, int(row["version"])

    def read_handle(self, case_id: str) -> dict:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT body FROM review_handles WHERE case_id=%s",
                (case_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return {}
        body = row["body"]
        if isinstance(body, str):
            import json

            body = json.loads(body)
        if not isinstance(body, dict):
            raise ValueError(f"review handle {case_id} is not an object")
        return body

    def write_handle(self, case_id: str, handle: dict) -> None:
        from psycopg.types.json import Json

        conn = self._conn()
        try:
            with conn.transaction():
                conn.execute(
                    """
                    INSERT INTO review_handles (case_id, body) VALUES (%s, %s)
                    ON CONFLICT (case_id) DO UPDATE SET body = EXCLUDED.body
                    """,
                    (case_id, Json(handle)),
                )
        finally:
            conn.close()

    def result_for(self, case_id: str) -> dict | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT body FROM review_results WHERE case_id=%s",
                (case_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        body = row["body"]
        if isinstance(body, str):
            import json

            body = json.loads(body)
        return body

    def draft_case_ids(self) -> list[str]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT DISTINCT case_id FROM review_drafts ORDER BY case_id"
            ).fetchall()
        finally:
            conn.close()
        return [row[CASE_ID] for row in rows]

    def open_cases(self) -> list[dict]:
        """Sidebar rows: latest classification, without created or declined cases.

        Both reads use one connection. review_results holds the latest draft body.
        """
        conn = self._conn()
        try:
            drafts = conn.execute(
                """
                SELECT case_id, body->>%s AS type_value
                FROM review_results
                ORDER BY case_id
                """,
                (CLASSIFICATION,),
            ).fetchall()
            finished = conn.execute(
                """
                SELECT case_id FROM records
                UNION
                SELECT case_id FROM reviews
                GROUP BY case_id
                HAVING BOOL_AND(status = 'rejected')
                UNION
                SELECT h.case_id
                FROM review_handles AS h
                JOIN reviews AS rv ON rv.id = h.body->>%s
                WHERE rv.status = 'rejected'
                """,
                ("review_id",),
            ).fetchall()
        finally:
            conn.close()
        done = {row[CASE_ID] for row in finished if isinstance(row[CASE_ID], str)}
        rows = []
        for row in drafts:
            case_id = row[CASE_ID]
            if not isinstance(case_id, str) or case_id in done:
                continue
            type_value = row["type_value"]
            rows.append({"id": case_id, "type_value": type_value if isinstance(type_value, str) else ""})
        return rows

    def clear(self) -> None:
        """Remove review rows. Tests use this on a disposable database."""
        conn = self._conn()
        try:
            conn.execute("DELETE FROM review_drafts")
            conn.execute("DELETE FROM review_handles")
            conn.execute("DELETE FROM review_results")
            conn.commit()
        finally:
            conn.close()
