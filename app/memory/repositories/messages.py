import sqlite3
from collections.abc import Callable

from app.memory.models import Message
from app.memory.utils import now_iso
from app.text import sanitize_text


class MessageRepository:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self.connect = connect

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
                SELECT id, session_id, role, content, created_at
                FROM messages
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        return [
            Message(row["session_id"], row["role"], row["content"], row["created_at"], id=row["id"])
            for row in reversed(rows)
        ]

    def get_session_messages(self, session_id: str) -> list[Message]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, session_id, role, content, created_at
                FROM messages
                WHERE session_id = ?
                ORDER BY id ASC
                """,
                (session_id,),
            ).fetchall()
        return [
            Message(row["session_id"], row["role"], row["content"], row["created_at"], id=row["id"])
            for row in rows
        ]
