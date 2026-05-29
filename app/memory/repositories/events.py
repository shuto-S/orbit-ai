import json
import sqlite3
from collections.abc import Callable
from typing import Any

from app.memory.models import DecisionLog
from app.memory.utils import now_iso
from app.text import sanitize_text


class EventRepository:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self.connect = connect

    def add_proactive_event(
        self,
        proposed_text: str,
        outcome: str | None = None,
        user_response: str | None = None,
        memory_id: int | None = None,
    ) -> None:
        safe_proposed_text = sanitize_text(proposed_text)
        safe_user_response = sanitize_text(user_response) if user_response is not None else None
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO proactive_events (memory_id, proposed_text, user_response, outcome, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (memory_id, safe_proposed_text, safe_user_response, outcome, now_iso()),
            )

    def add_decision_log(
        self,
        kind: str,
        decision: str,
        reason: str,
        session_id: str | None = None,
        task_id: int | None = None,
        candidate_text: str | None = None,
        score: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO decision_logs
                (kind, session_id, task_id, candidate_text, decision, reason, score, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sanitize_text(kind),
                    session_id,
                    task_id,
                    sanitize_text(candidate_text) if candidate_text is not None else None,
                    sanitize_text(decision),
                    sanitize_text(reason),
                    score,
                    metadata_json,
                    now_iso(),
                ),
            )

    def recent_proactive_events(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, memory_id, proposed_text, user_response, outcome, created_at
                FROM proactive_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def recent_decision_logs(self, limit: int = 20) -> list[DecisionLog]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, kind, session_id, task_id, candidate_text, decision, reason,
                       score, metadata_json, created_at
                FROM decision_logs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            DecisionLog(
                id=row["id"],
                kind=row["kind"],
                session_id=row["session_id"],
                task_id=row["task_id"],
                candidate_text=row["candidate_text"],
                decision=row["decision"],
                reason=row["reason"],
                score=row["score"],
                metadata_json=row["metadata_json"],
                created_at=row["created_at"],
            )
            for row in rows
        ]
