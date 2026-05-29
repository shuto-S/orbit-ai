import sqlite3
from collections.abc import Callable

from app.memory.utils import now_iso
from app.text import sanitize_text


class CodexThreadRepository:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self.connect = connect

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
