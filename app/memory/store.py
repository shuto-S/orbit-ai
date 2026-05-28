import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.paths import DB_PATH, REPO_ROOT
from app.text import sanitize_text


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def parse_due_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def utc_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


@dataclass(frozen=True)
class Message:
    session_id: str
    role: str
    content: str
    created_at: str


@dataclass(frozen=True)
class Memory:
    id: int
    kind: str
    content: str
    priority: float
    confidence: float
    created_at: str


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    summary: str
    open_loops: list[str]
    decisions: list[str]
    follow_up_candidates: list[str]
    created_at: str


@dataclass(frozen=True)
class Task:
    id: int
    title: str
    description: str | None
    status: str
    priority: float
    due_at: str | None
    source: str | None
    source_session_id: str | None
    created_at: str
    updated_at: str


class MemoryStore:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        schema_path = REPO_ROOT / "app" / "memory" / "schema.sql"
        with self.connect() as connection:
            connection.executescript(schema_path.read_text(encoding="utf-8"))

    def add_message(self, session_id: str, role: str, content: str) -> None:
        safe_content = sanitize_text(content)
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (session_id, role, safe_content, now_iso()),
            )

    def get_recent_messages(self, session_id: str, limit: int = 12) -> list[Message]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT session_id, role, content, created_at
                FROM messages
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        return [Message(row["session_id"], row["role"], row["content"], row["created_at"]) for row in reversed(rows)]

    def get_session_messages(self, session_id: str) -> list[Message]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT session_id, role, content, created_at
                FROM messages
                WHERE session_id = ?
                ORDER BY id ASC
                """,
                (session_id,),
            ).fetchall()
        return [Message(row["session_id"], row["role"], row["content"], row["created_at"]) for row in rows]

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
                open_loops=self._loads_list(row["open_loops"]),
                decisions=self._loads_list(row["decisions"]),
                follow_up_candidates=self._loads_list(row["follow_up_candidates"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def add_memory(self, kind: str, content: str, priority: float = 0.5, confidence: float = 0.8) -> None:
        safe_content = sanitize_text(content)
        if self.memory_exists(safe_content):
            return
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO memories (kind, content, priority, confidence, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (kind, safe_content, priority, confidence, now_iso()),
            )

    def memory_exists(self, content: str) -> bool:
        with self.connect() as connection:
            row = connection.execute("SELECT 1 FROM memories WHERE content = ? LIMIT 1", (content,)).fetchone()
        return row is not None

    def list_memories(self, limit: int = 20) -> list[Memory]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, kind, content, priority, confidence, created_at
                FROM memories
                ORDER BY priority DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            Memory(row["id"], row["kind"], row["content"], row["priority"], row["confidence"], row["created_at"])
            for row in rows
        ]

    def search_memories(self, _query: str, limit: int = 6) -> list[Memory]:
        return self.list_memories(limit)

    def latest_open_loops(self, limit: int = 5) -> list[str]:
        return self.list_open_task_titles_for_proactive(datetime.now(UTC), limit=limit)

    def list_open_task_titles_for_proactive(self, now: datetime, limit: int = 5) -> list[str]:
        loops: list[str] = []
        for task in self.list_due_tasks(now, limit=limit):
            loops.append(task.title)
            if len(loops) >= limit:
                return loops[:limit]
        for task in self.list_tasks(statuses=("open",), limit=limit):
            loops.append(task.title)
            if len(loops) >= limit:
                return loops[:limit]
        known_task_titles = self.task_titles()
        for summary in self.list_summaries(limit=20):
            for title in [*summary.open_loops, *summary.follow_up_candidates]:
                if title in known_task_titles:
                    continue
                loops.append(title)
                if len(loops) >= limit:
                    return loops[:limit]
        return loops[:limit]

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

    def add_tasks_from_summary(
        self,
        session_id: str,
        open_loops: list[str],
        follow_up_candidates: list[str],
    ) -> int:
        created = 0
        for title in open_loops:
            if self.add_task(title=title, source="open_loop", source_session_id=session_id) is not None:
                created += 1
        for title in follow_up_candidates:
            if self.add_task(title=title, source="follow_up_candidate", source_session_id=session_id) is not None:
                created += 1
        return created

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
        return self._update_task_status(task_id, "done")

    def snooze_task(self, task_id: int, due_at: str) -> bool:
        safe_due_at = sanitize_text(due_at).strip()
        if not safe_due_at:
            return False
        return self._update_task_status(task_id, "snoozed", safe_due_at, allowed_statuses=("open", "snoozed"))

    def _update_task_status(
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

    def get_codex_thread_id(self, session_id: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT codex_thread_id FROM codex_threads WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return str(row["codex_thread_id"])

    def set_codex_thread_id(self, session_id: str, codex_thread_id: str) -> None:
        now = now_iso()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO codex_threads (session_id, codex_thread_id, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                  codex_thread_id = excluded.codex_thread_id,
                  updated_at = excluded.updated_at
                """,
                (session_id, sanitize_text(codex_thread_id), now, now),
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

    @staticmethod
    def _loads_list(value: str | None) -> list[str]:
        if not value:
            return []
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError:
            return []
        if not isinstance(loaded, list):
            return []
        return [str(item) for item in loaded if str(item).strip()]
