import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.memory.models import Task
from app.memory.utils import now_iso, parse_due_at, utc_aware
from app.text import sanitize_text


class TaskRepository:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self.connect = connect

    def list_due_tasks(self, now: datetime, limit: int = 5) -> list[Task]:
        now = utc_aware(now)
        due_tasks: list[Task] = []
        for task in self.list_tasks(statuses=("snoozed",), limit=1000):
            due_at = parse_due_at(task.due_at)
            if due_at is None:
                continue
            if due_at <= now:
                due_tasks.append(task)
        due_tasks.sort(key=lambda task: (parse_due_at(task.due_at) or datetime.max.replace(tzinfo=UTC), -task.priority))
        return due_tasks[:limit]

    def add_task(
        self,
        title: str,
        source: str,
        source_session_id: str | None = None,
        description: str | None = None,
        priority: float = 0.5,
        due_at: str | None = None,
    ) -> int | None:
        safe_title = sanitize_text(title).strip()
        if not safe_title:
            return None
        if self.task_exists(safe_title):
            return None
        now = now_iso()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO tasks
                (title, description, status, priority, due_at, source, source_session_id, created_at, updated_at)
                VALUES (?, ?, 'open', ?, ?, ?, ?, ?, ?)
                """,
                (
                    safe_title,
                    sanitize_text(description) if description else None,
                    priority,
                    sanitize_text(due_at) if due_at else None,
                    sanitize_text(source),
                    source_session_id,
                    now,
                    now,
                ),
            )
        return int(cursor.lastrowid)

    def task_exists(self, title: str) -> bool:
        safe_title = sanitize_text(title).strip()
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM tasks
                WHERE title = ?
                  AND status IN ('open', 'snoozed')
                LIMIT 1
                """,
                (safe_title,),
            ).fetchone()
        return row is not None

    def task_titles(self) -> set[str]:
        with self.connect() as connection:
            rows = connection.execute("SELECT title FROM tasks").fetchall()
        return {str(row["title"]) for row in rows}

    def list_tasks(self, statuses: tuple[str, ...] | None = None, limit: int = 20) -> list[Task]:
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
                SELECT id, title, description, status, priority, due_at, source,
                       source_session_id, created_at, updated_at
                FROM tasks
                {where}
                ORDER BY
                  CASE status WHEN 'open' THEN 0 WHEN 'snoozed' THEN 1 ELSE 2 END,
                  priority DESC,
                  id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [
            Task(
                id=row["id"],
                title=row["title"],
                description=row["description"],
                status=row["status"],
                priority=row["priority"],
                due_at=row["due_at"],
                source=row["source"],
                source_session_id=row["source_session_id"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def mark_task_done(self, task_id: int) -> bool:
        return self.update_task_status(task_id, "done")

    def snooze_task(self, task_id: int, due_at: str) -> bool:
        safe_due_at = sanitize_text(due_at).strip()
        if not safe_due_at:
            return False
        return self.update_task_status(task_id, "snoozed", safe_due_at, allowed_statuses=("open", "snoozed"))

    def update_task_status(
        self,
        task_id: int,
        status: str,
        due_at: str | None = None,
        allowed_statuses: tuple[str, ...] | None = None,
    ) -> bool:
        status_filter = ""
        params: list[Any] = [status, due_at, now_iso(), task_id]
        if allowed_statuses:
            placeholders = ", ".join("?" for _ in allowed_statuses)
            status_filter = f" AND status IN ({placeholders})"
            params.extend(allowed_statuses)
        with self.connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE tasks
                SET status = ?, due_at = ?, updated_at = ?
                WHERE id = ?
                {status_filter}
                """,
                params,
            )
        return cursor.rowcount > 0
