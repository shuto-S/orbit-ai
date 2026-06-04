import json
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from app.memory.models import AutonomousJob, AutonomousJobRun, AutonomousNotification
from app.memory.utils import now_iso, utc_aware
from app.text import sanitize_text


class AutonomousRepository:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self.connect = connect

    def add_job(
        self,
        kind: str,
        title: str,
        schedule_type: str,
        next_run_at: str | None,
        interval_seconds: int | None = None,
        timezone: str = "Asia/Tokyo",
        payload: dict[str, Any] | None = None,
        source: str | None = None,
        source_session_id: str | None = None,
        status: str = "active",
    ) -> int | None:
        safe_kind = sanitize_text(kind).strip()
        safe_title = sanitize_text(title).strip()
        if not safe_kind or not safe_title or schedule_type not in {"once", "interval"}:
            return None
        if schedule_type == "interval" and (interval_seconds is None or interval_seconds <= 0):
            return None
        timestamp = now_iso()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO autonomous_jobs
                (kind, title, status, schedule_type, next_run_at, interval_seconds, timezone,
                 payload_json, source, source_session_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    safe_kind,
                    safe_title,
                    status,
                    schedule_type,
                    next_run_at,
                    interval_seconds,
                    sanitize_text(timezone).strip() or "Asia/Tokyo",
                    json.dumps(payload or {}, ensure_ascii=False),
                    sanitize_text(source) if source else None,
                    source_session_id,
                    timestamp,
                    timestamp,
                ),
            )
        return int(cursor.lastrowid)

    def get_job(self, job_id: int) -> AutonomousJob | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, kind, title, status, schedule_type, next_run_at, interval_seconds,
                       timezone, payload_json, source, source_session_id, locked_until,
                       lock_owner, last_run_at, last_error, created_at, updated_at
                FROM autonomous_jobs
                WHERE id = ?
                """,
                (job_id,),
            ).fetchone()
        return _row_to_job(row) if row is not None else None

    def list_jobs(self, statuses: tuple[str, ...] | None = None, limit: int = 20) -> list[AutonomousJob]:
        params: list[Any] = []
        where = ""
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            where = f"WHERE status IN ({placeholders})"
            params.extend(statuses)
        params.append(limit)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, kind, title, status, schedule_type, next_run_at, interval_seconds,
                       timezone, payload_json, source, source_session_id, locked_until,
                       lock_owner, last_run_at, last_error, created_at, updated_at
                FROM autonomous_jobs
                {where}
                ORDER BY
                  CASE status WHEN 'active' THEN 0 WHEN 'paused' THEN 1 ELSE 2 END,
                  next_run_at IS NULL,
                  next_run_at,
                  id
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [_row_to_job(row) for row in rows]

    def claim_due_jobs(
        self,
        now: datetime,
        lock_owner: str,
        limit: int = 5,
        lock_seconds: int = 60,
    ) -> list[AutonomousJob]:
        now_text = utc_aware(now).isoformat()
        locked_until = (utc_aware(now) + timedelta(seconds=lock_seconds)).isoformat()
        timestamp = now_iso()
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id
                FROM autonomous_jobs
                WHERE status = 'active'
                  AND next_run_at IS NOT NULL
                  AND next_run_at <= ?
                  AND (locked_until IS NULL OR locked_until <= ?)
                ORDER BY next_run_at, id
                LIMIT ?
                """,
                (now_text, now_text, limit),
            ).fetchall()
            ids = [int(row["id"]) for row in rows]
            if not ids:
                return []
            placeholders = ", ".join("?" for _ in ids)
            connection.execute(
                f"""
                UPDATE autonomous_jobs
                SET locked_until = ?, lock_owner = ?, updated_at = ?
                WHERE id IN ({placeholders})
                """,
                [locked_until, lock_owner, timestamp, *ids],
            )
        return [job for job_id in ids if (job := self.get_job(job_id)) is not None]

    def finish_job_success(
        self,
        job_id: int,
        last_run_at: datetime,
        next_run_at: str | None,
        status: str,
    ) -> None:
        timestamp = now_iso()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE autonomous_jobs
                SET status = ?, next_run_at = ?, locked_until = NULL, lock_owner = NULL,
                    last_run_at = ?, last_error = NULL, updated_at = ?
                WHERE id = ?
                """,
                (status, next_run_at, utc_aware(last_run_at).isoformat(), timestamp, job_id),
            )

    def finish_job_failure(
        self,
        job_id: int,
        last_run_at: datetime,
        next_run_at: str | None,
        error: str,
        status: str = "active",
    ) -> None:
        timestamp = now_iso()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE autonomous_jobs
                SET status = ?, next_run_at = ?, locked_until = NULL, lock_owner = NULL,
                    last_run_at = ?, last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    next_run_at,
                    utc_aware(last_run_at).isoformat(),
                    sanitize_text(error),
                    timestamp,
                    job_id,
                ),
            )

    def update_job_status(self, job_id: int, status: str) -> AutonomousJob | None:
        if status not in {"active", "paused", "completed", "cancelled", "failed"}:
            return None
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE autonomous_jobs
                SET status = ?, locked_until = NULL, lock_owner = NULL, updated_at = ?
                WHERE id = ?
                """,
                (status, now_iso(), job_id),
            )
        if cursor.rowcount <= 0:
            return None
        return self.get_job(job_id)

    def add_job_run(
        self,
        job_id: int,
        status: str,
        started_at: datetime,
        completed_at: datetime,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO autonomous_job_runs
                (job_id, status, started_at, completed_at, result_json, error)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    status,
                    utc_aware(started_at).isoformat(),
                    utc_aware(completed_at).isoformat(),
                    json.dumps(result or {}, ensure_ascii=False),
                    sanitize_text(error) if error else None,
                ),
            )
        return int(cursor.lastrowid)

    def list_job_runs(self, job_id: int | None = None, limit: int = 20) -> list[AutonomousJobRun]:
        params: list[Any] = []
        where = ""
        if job_id is not None:
            where = "WHERE job_id = ?"
            params.append(job_id)
        params.append(limit)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, job_id, status, started_at, completed_at, result_json, error
                FROM autonomous_job_runs
                {where}
                ORDER BY id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [_row_to_job_run(row) for row in rows]

    def add_notification(
        self,
        title: str,
        body: str,
        job_id: int | None = None,
        priority: float = 0.5,
        sources: list[dict[str, Any]] | None = None,
    ) -> int | None:
        safe_title = sanitize_text(title).strip()
        safe_body = sanitize_text(body).strip()
        if not safe_title or not safe_body:
            return None
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO autonomous_notifications
                (job_id, title, body, status, priority, sources_json, created_at)
                VALUES (?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    job_id,
                    safe_title,
                    safe_body,
                    priority,
                    json.dumps(sources or [], ensure_ascii=False),
                    now_iso(),
                ),
            )
        return int(cursor.lastrowid)

    def list_notifications(self, status: str | None = None, limit: int = 20) -> list[AutonomousNotification]:
        params: list[Any] = []
        where = ""
        if status:
            where = "WHERE status = ?"
            params.append(status)
        params.append(limit)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, job_id, title, body, status, priority, sources_json, created_at, delivered_at
                FROM autonomous_notifications
                {where}
                ORDER BY
                  CASE status WHEN 'pending' THEN 0 ELSE 1 END,
                  id
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [_row_to_notification(row) for row in rows]

    def mark_notification_delivered(
        self,
        notification_id: int,
        delivered_at: datetime,
    ) -> AutonomousNotification | None:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE autonomous_notifications
                SET status = 'delivered', delivered_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (utc_aware(delivered_at).isoformat(), notification_id),
            )
        if cursor.rowcount <= 0:
            return None
        return self.get_notification(notification_id)

    def get_notification(self, notification_id: int) -> AutonomousNotification | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, job_id, title, body, status, priority, sources_json, created_at, delivered_at
                FROM autonomous_notifications
                WHERE id = ?
                """,
                (notification_id,),
            ).fetchone()
        return _row_to_notification(row) if row is not None else None


def _row_to_job(row: sqlite3.Row) -> AutonomousJob:
    return AutonomousJob(
        id=row["id"],
        kind=row["kind"],
        title=row["title"],
        status=row["status"],
        schedule_type=row["schedule_type"],
        next_run_at=row["next_run_at"],
        interval_seconds=row["interval_seconds"],
        timezone=row["timezone"],
        payload=_loads_object(row["payload_json"]),
        source=row["source"],
        source_session_id=row["source_session_id"],
        locked_until=row["locked_until"],
        lock_owner=row["lock_owner"],
        last_run_at=row["last_run_at"],
        last_error=row["last_error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_job_run(row: sqlite3.Row) -> AutonomousJobRun:
    return AutonomousJobRun(
        id=row["id"],
        job_id=row["job_id"],
        status=row["status"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        result=_loads_object(row["result_json"]),
        error=row["error"],
    )


def _row_to_notification(row: sqlite3.Row) -> AutonomousNotification:
    return AutonomousNotification(
        id=row["id"],
        job_id=row["job_id"],
        title=row["title"],
        body=row["body"],
        status=row["status"],
        priority=row["priority"],
        sources=_loads_list(row["sources_json"]),
        created_at=row["created_at"],
        delivered_at=row["delivered_at"],
    )


def _loads_object(value: str | None) -> dict[str, Any]:
    try:
        loaded = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _loads_list(value: str | None) -> list[dict[str, Any]]:
    try:
        loaded = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []
    return [item for item in loaded if isinstance(item, dict)]
