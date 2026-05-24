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
        loops: list[str] = []
        for summary in self.list_summaries(limit=20):
            loops.extend(summary.open_loops)
            loops.extend(summary.follow_up_candidates)
            if len(loops) >= limit:
                break
        return loops[:limit]

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
