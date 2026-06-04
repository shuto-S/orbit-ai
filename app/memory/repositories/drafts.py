from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from app.memory.models import Draft
from app.memory.utils import dumps_dict, loads_dict, now_iso
from app.text import sanitize_text

ALLOWED_DRAFT_STATUSES = ("draft", "accepted", "rejected", "archived")


class DraftRepository:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self.connect = connect

    def add_draft(
        self,
        kind: str,
        title: str,
        body: str,
        source_session_id: str | None = None,
        source_message_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int | None:
        safe_title = sanitize_text(title).strip()
        safe_body = sanitize_text(body).strip()
        if not safe_title or not safe_body:
            return None
        now = now_iso()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO drafts
                (kind, title, body, status, source_session_id, source_message_id,
                 created_at, updated_at, metadata_json)
                VALUES (?, ?, ?, 'draft', ?, ?, ?, ?, ?)
                """,
                (
                    sanitize_text(kind).strip() or "text",
                    safe_title,
                    safe_body,
                    sanitize_text(source_session_id) if source_session_id else None,
                    source_message_id,
                    now,
                    now,
                    dumps_dict(metadata or {}),
                ),
            )
        return int(cursor.lastrowid)

    def list_drafts(self, status: str = "draft", limit: int = 20) -> list[Draft]:
        safe_status = _status(status)
        safe_limit = max(0, int(limit))
        if safe_limit == 0:
            return []
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, kind, title, body, status, source_session_id, source_message_id,
                       created_at, updated_at, metadata_json
                FROM drafts
                WHERE status = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (safe_status, safe_limit),
            ).fetchall()
        return [_row_to_draft(row) for row in rows]

    def get_draft(self, draft_id: int) -> Draft | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, kind, title, body, status, source_session_id, source_message_id,
                       created_at, updated_at, metadata_json
                FROM drafts
                WHERE id = ?
                """,
                (draft_id,),
            ).fetchone()
        return _row_to_draft(row) if row is not None else None

    def update_draft_status(self, draft_id: int, status: str) -> Draft | None:
        safe_status = _status(status)
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE drafts
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                (safe_status, now_iso(), draft_id),
            )
        if cursor.rowcount <= 0:
            return None
        return self.get_draft(draft_id)

    def archive_draft(self, draft_id: int) -> Draft | None:
        return self.update_draft_status(draft_id, "archived")


def _row_to_draft(row: sqlite3.Row) -> Draft:
    return Draft(
        id=row["id"],
        kind=row["kind"],
        title=row["title"],
        body=row["body"],
        status=row["status"],
        source_session_id=row["source_session_id"],
        source_message_id=row["source_message_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        metadata=loads_dict(row["metadata_json"]),
    )


def _status(value: str) -> str:
    safe_value = sanitize_text(value).strip().lower()
    return safe_value if safe_value in ALLOWED_DRAFT_STATUSES else "draft"
