from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from app.memory.models import ApprovalRequest
from app.memory.utils import dumps_dict, loads_dict, now_iso
from app.text import sanitize_text

ALLOWED_APPROVAL_STATUSES = ("pending", "approved", "rejected", "expired", "executed", "failed")
ALLOWED_RISK_LEVELS = ("low", "normal", "high")


class ApprovalRequestRepository:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self.connect = connect

    def add_approval_request(
        self,
        action: str,
        payload: dict[str, Any],
        reason: str,
        risk_level: str = "normal",
        source_session_id: str | None = None,
        source_message_id: int | None = None,
        expires_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        now = now_iso()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO approval_requests
                (action, payload_json, reason, risk_level, status, source_session_id,
                 source_message_id, created_at, updated_at, expires_at, metadata_json)
                VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)
                """,
                (
                    sanitize_text(action).strip() or "unknown",
                    dumps_dict(payload),
                    sanitize_text(reason).strip() or "approval required",
                    _risk_level(risk_level),
                    sanitize_text(source_session_id) if source_session_id else None,
                    source_message_id,
                    now,
                    now,
                    sanitize_text(expires_at) if expires_at else None,
                    dumps_dict(metadata or {}),
                ),
            )
        return int(cursor.lastrowid)

    def list_approval_requests(self, status: str = "pending", limit: int = 20) -> list[ApprovalRequest]:
        safe_status = _status(status)
        safe_limit = max(0, int(limit))
        if safe_limit == 0:
            return []
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, action, payload_json, reason, risk_level, status, source_session_id,
                       source_message_id, created_at, updated_at, expires_at, metadata_json
                FROM approval_requests
                WHERE status = ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (safe_status, safe_limit),
            ).fetchall()
        return [_row_to_approval_request(row) for row in rows]

    def get_approval_request(self, request_id: int) -> ApprovalRequest | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, action, payload_json, reason, risk_level, status, source_session_id,
                       source_message_id, created_at, updated_at, expires_at, metadata_json
                FROM approval_requests
                WHERE id = ?
                """,
                (request_id,),
            ).fetchone()
        return _row_to_approval_request(row) if row is not None else None

    def approve_request(self, request_id: int) -> ApprovalRequest | None:
        return self.update_approval_request_status(request_id, "approved", allowed_statuses=("pending",))

    def reject_request(self, request_id: int) -> ApprovalRequest | None:
        return self.update_approval_request_status(request_id, "rejected", allowed_statuses=("pending",))

    def update_approval_request_status(
        self,
        request_id: int,
        status: str,
        allowed_statuses: tuple[str, ...] | None = None,
    ) -> ApprovalRequest | None:
        safe_status = _status(status)
        status_filter = ""
        params: list[Any] = [safe_status, now_iso(), request_id]
        if allowed_statuses:
            safe_allowed = tuple(_status(value) for value in allowed_statuses)
            placeholders = ", ".join("?" for _ in safe_allowed)
            status_filter = f"AND status IN ({placeholders})"
            params.extend(safe_allowed)
        with self.connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE approval_requests
                SET status = ?, updated_at = ?
                WHERE id = ?
                {status_filter}
                """,
                params,
            )
        if cursor.rowcount <= 0:
            return None
        return self.get_approval_request(request_id)


def _row_to_approval_request(row: sqlite3.Row) -> ApprovalRequest:
    return ApprovalRequest(
        id=row["id"],
        action=row["action"],
        payload=loads_dict(row["payload_json"]),
        reason=row["reason"],
        risk_level=row["risk_level"],
        status=row["status"],
        source_session_id=row["source_session_id"],
        source_message_id=row["source_message_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        expires_at=row["expires_at"],
        metadata=loads_dict(row["metadata_json"]),
    )


def _risk_level(value: str) -> str:
    safe_value = sanitize_text(value).strip().lower()
    return safe_value if safe_value in ALLOWED_RISK_LEVELS else "normal"


def _status(value: str) -> str:
    safe_value = sanitize_text(value).strip().lower()
    return safe_value if safe_value in ALLOWED_APPROVAL_STATUSES else "pending"
