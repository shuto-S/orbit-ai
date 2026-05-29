import json
import sqlite3
from collections.abc import Callable

from app.memory.models import SessionSummary
from app.memory.utils import loads_list, now_iso
from app.text import sanitize_text


class SummaryRepository:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self.connect = connect

    def add_summary(
        self,
        session_id: str,
        summary: str,
        open_loops: list[str],
        decisions: list[str],
        follow_up_candidates: list[str],
    ) -> None:
        safe_open_loops = [sanitize_text(value) for value in open_loops]
        safe_decisions = [sanitize_text(value) for value in decisions]
        safe_follow_up_candidates = [sanitize_text(value) for value in follow_up_candidates]
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO session_summaries
                (session_id, summary, open_loops, decisions, follow_up_candidates, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    sanitize_text(summary),
                    json.dumps(safe_open_loops, ensure_ascii=False),
                    json.dumps(safe_decisions, ensure_ascii=False),
                    json.dumps(safe_follow_up_candidates, ensure_ascii=False),
                    now_iso(),
                ),
            )

    def list_summaries(self, limit: int = 5) -> list[SessionSummary]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT session_id, summary, open_loops, decisions, follow_up_candidates, created_at
                FROM session_summaries
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            SessionSummary(
                session_id=row["session_id"],
                summary=row["summary"],
                open_loops=loads_list(row["open_loops"]),
                decisions=loads_list(row["decisions"]),
                follow_up_candidates=loads_list(row["follow_up_candidates"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]
