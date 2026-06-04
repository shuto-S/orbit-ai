from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import datetime
from typing import Any

from app.memory.merger import is_sensitive_text, normalize_memory_text
from app.memory.models import OpenLoop
from app.memory.utils import now_iso, utc_aware
from app.text import sanitize_text

ACTIVE_STATUSES = ("open", "snoozed")
ALLOWED_STATUSES = ("open", "snoozed", "resolved", "archived")


class OpenLoopRepository:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self.connect = connect

    def add_open_loop(
        self,
        title: str,
        summary: str | None = None,
        source_session_id: str | None = None,
        source_message_id: int | None = None,
        suggested_next_step: str | None = None,
        importance: float = 0.5,
        confidence: float = 0.5,
        due_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int | None:
        safe_title = sanitize_text(title).strip()
        safe_summary = sanitize_text(summary or title).strip()
        safe_next_step = sanitize_text(suggested_next_step).strip() if suggested_next_step else None
        if not safe_title or not safe_summary:
            return None
        if any(is_sensitive_text(value) for value in (safe_title, safe_summary, safe_next_step or "")):
            return None
        if self.open_loop_exists(safe_title):
            return None

        now = now_iso()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO open_loops
                (title, summary, status, importance, confidence, source_session_id, source_message_id,
                 suggested_next_step, due_at, last_discussed_at, created_at, updated_at, metadata_json)
                VALUES (?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    safe_title,
                    safe_summary,
                    _clamp_score(importance),
                    _clamp_score(confidence),
                    sanitize_text(source_session_id) if source_session_id else None,
                    source_message_id,
                    safe_next_step,
                    sanitize_text(due_at) if due_at else None,
                    now,
                    now,
                    now,
                    _metadata_json(metadata),
                ),
            )
        return int(cursor.lastrowid)

    def open_loop_exists(self, title: str, statuses: tuple[str, ...] = ACTIVE_STATUSES) -> bool:
        normalized = normalize_memory_text(sanitize_text(title))
        if not normalized or not statuses:
            return False
        placeholders = ", ".join("?" for _ in statuses)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT title
                FROM open_loops
                WHERE status IN ({placeholders})
                """,
                statuses,
            ).fetchall()
        return any(normalize_memory_text(row["title"]) == normalized for row in rows)

    def get_open_loop(self, loop_id: int) -> OpenLoop | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, title, summary, status, importance, confidence, source_session_id,
                       source_message_id, suggested_next_step, due_at, last_discussed_at,
                       created_at, updated_at, metadata_json
                FROM open_loops
                WHERE id = ?
                """,
                (loop_id,),
            ).fetchone()
        return _row_to_open_loop(row) if row is not None else None

    def list_open_loops(self, statuses: tuple[str, ...] = ("open",), limit: int = 20) -> list[OpenLoop]:
        safe_statuses = tuple(status for status in statuses if status in ALLOWED_STATUSES)
        safe_limit = max(0, int(limit))
        if not safe_statuses or safe_limit == 0:
            return []
        placeholders = ", ".join("?" for _ in safe_statuses)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, title, summary, status, importance, confidence, source_session_id,
                       source_message_id, suggested_next_step, due_at, last_discussed_at,
                       created_at, updated_at, metadata_json
                FROM open_loops
                WHERE status IN ({placeholders})
                ORDER BY
                  CASE status WHEN 'open' THEN 0 WHEN 'snoozed' THEN 1 ELSE 2 END,
                  importance DESC,
                  updated_at DESC,
                  id DESC
                LIMIT ?
                """,
                (*safe_statuses, safe_limit),
            ).fetchall()
        return [_row_to_open_loop(row) for row in rows]

    def update_open_loop_status(self, loop_id: int, status: str) -> bool:
        if status not in ALLOWED_STATUSES:
            return False
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE open_loops
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, now_iso(), loop_id),
            )
        return cursor.rowcount > 0

    def resolve_open_loop(self, loop_id: int) -> bool:
        return self.update_open_loop_status(loop_id, "resolved")

    def archive_open_loop(self, loop_id: int) -> bool:
        return self.update_open_loop_status(loop_id, "archived")

    def touch_open_loop(self, loop_id: int, now: datetime | None = None) -> bool:
        timestamp = utc_aware(now).isoformat() if now is not None else now_iso()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE open_loops
                SET last_discussed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (timestamp, timestamp, loop_id),
            )
        return cursor.rowcount > 0


def _row_to_open_loop(row: sqlite3.Row) -> OpenLoop:
    return OpenLoop(
        id=row["id"],
        title=row["title"],
        summary=row["summary"],
        status=row["status"],
        importance=row["importance"],
        confidence=row["confidence"],
        source_session_id=row["source_session_id"],
        source_message_id=row["source_message_id"],
        suggested_next_step=row["suggested_next_step"],
        due_at=row["due_at"],
        last_discussed_at=row["last_discussed_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        metadata=_loads_metadata(row["metadata_json"]),
    )


def _clamp_score(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def _metadata_json(metadata: dict[str, Any] | None) -> str:
    if metadata is None:
        return "{}"
    return json.dumps(metadata, ensure_ascii=False, sort_keys=True)


def _loads_metadata(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}
