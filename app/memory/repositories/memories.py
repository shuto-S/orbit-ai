import sqlite3
from collections.abc import Callable

from app.memory.models import Memory
from app.memory.utils import now_iso
from app.text import sanitize_text


class MemoryRepository:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self.connect = connect

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
