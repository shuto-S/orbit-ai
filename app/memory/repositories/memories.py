from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from typing import Any

from app.memory.merger import is_sensitive_text, looks_contradictory, normalize_memory_text
from app.memory.models import Memory
from app.memory.utils import loads_list, now_iso
from app.text import sanitize_text

ACTIVE_STATUSES = ("active",)
VISIBLE_STATUSES = ("active", "archived")
KIND_WEIGHTS = {
    "preference": 1.0,
    "profile": 0.9,
    "project": 0.75,
    "decision": 0.7,
    "relationship": 0.65,
    "manual": 0.6,
    "open_loop": 0.35,
}


class MemoryRepository:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self.connect = connect

    def add_memory(
        self,
        kind: str,
        content: str,
        priority: float = 0.5,
        confidence: float = 0.8,
        source_session_id: str | None = None,
        source_message_ids: list[int] | None = None,
        sensitivity: str = "normal",
        expires_at: str | None = None,
        status: str = "active",
    ) -> int | None:
        safe_content = sanitize_text(content).strip()
        if not safe_content:
            return None
        if sensitivity == "sensitive" or is_sensitive_text(safe_content):
            return None
        safe_kind = sanitize_text(kind).strip() or "note"
        safe_status = status if status in ("active", "archived", "forgotten") else "active"
        safe_sensitivity = sensitivity if sensitivity in ("normal", "sensitive") else "normal"
        now = now_iso()
        with self.connect() as connection:
            duplicate = self._find_duplicate(connection, safe_content)
            if duplicate is not None:
                self._update_existing(connection, duplicate, priority, confidence, now)
                return duplicate
            for memory in self._active_memories(connection, kind=safe_kind):
                if looks_contradictory(memory.content, safe_content):
                    self._set_status(connection, memory.id, "archived", now)
            cursor = connection.execute(
                """
                INSERT INTO memories
                (kind, content, priority, confidence, source_session_id, source_message_ids,
                 updated_at, use_count, status, sensitivity, expires_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
                """,
                (
                    safe_kind,
                    safe_content,
                    priority,
                    confidence,
                    source_session_id,
                    json.dumps(source_message_ids or [], ensure_ascii=False),
                    now,
                    safe_status,
                    safe_sensitivity,
                    sanitize_text(expires_at) if expires_at else None,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def memory_exists(self, content: str, statuses: tuple[str, ...] = ACTIVE_STATUSES) -> bool:
        safe_content = sanitize_text(content)
        with self.connect() as connection:
            duplicate = self._find_duplicate(connection, safe_content, statuses=statuses)
        return duplicate is not None

    def get_memory(self, memory_id: int) -> Memory | None:
        with self.connect() as connection:
            row = connection.execute(self._select_sql("WHERE id = ?"), (memory_id,)).fetchone()
        return self._row_to_memory(row) if row is not None else None

    def list_memories(
        self,
        limit: int = 20,
        statuses: tuple[str, ...] = ACTIVE_STATUSES,
    ) -> list[Memory]:
        placeholders = ",".join("?" for _ in statuses)
        with self.connect() as connection:
            rows = connection.execute(
                self._select_sql(
                    f"""
                    WHERE status IN ({placeholders})
                      AND (expires_at IS NULL OR expires_at = '' OR expires_at > ?)
                    ORDER BY priority DESC, confidence DESC, id DESC
                    LIMIT ?
                    """
                ),
                (*statuses, now_iso(), limit),
            ).fetchall()
        return [self._row_to_memory(row) for row in rows]

    def search_memories(self, query: str, limit: int = 6) -> list[Memory]:
        candidates = self.list_memories(limit=1000, statuses=ACTIVE_STATUSES)
        terms = _query_terms(query)
        scored: list[tuple[float, Memory]] = []
        for memory in candidates:
            match_score = self._match_score(memory, query, terms)
            if terms and match_score <= 0:
                continue
            scored.append((self._base_score(memory) + match_score, memory))
        scored.sort(key=lambda item: (item[0], item[1].updated_at or item[1].created_at, item[1].id), reverse=True)
        results = [memory for _, memory in scored[:limit]]
        if results:
            self._mark_used([memory.id for memory in results])
        return results

    def forget_memory(self, memory_id: int) -> bool:
        return self.update_memory_status(memory_id, "forgotten")

    def archive_memory(self, memory_id: int) -> bool:
        return self.update_memory_status(memory_id, "archived")

    def update_memory_status(self, memory_id: int, status: str) -> bool:
        if status not in ("active", "archived", "forgotten"):
            return False
        now = now_iso()
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE memories SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, memory_id),
            )
            return cursor.rowcount > 0

    def _find_duplicate(
        self,
        connection: sqlite3.Connection,
        content: str,
        statuses: tuple[str, ...] = ACTIVE_STATUSES,
    ) -> int | None:
        normalized = normalize_memory_text(content)
        for memory in self._memories_by_status(connection, statuses):
            if normalize_memory_text(memory.content) == normalized:
                return memory.id
        return None

    def _update_existing(
        self,
        connection: sqlite3.Connection,
        memory_id: int,
        priority: float,
        confidence: float,
        updated_at: str,
    ) -> None:
        connection.execute(
            """
            UPDATE memories
            SET priority = MAX(priority, ?),
                confidence = MAX(confidence, ?),
                updated_at = ?,
                status = 'active'
            WHERE id = ?
            """,
            (priority, confidence, updated_at, memory_id),
        )

    def _active_memories(self, connection: sqlite3.Connection, kind: str | None = None) -> list[Memory]:
        if kind:
            rows = connection.execute(self._select_sql("WHERE status = 'active' AND kind = ?"), (kind,)).fetchall()
        else:
            rows = connection.execute(self._select_sql("WHERE status = 'active'")).fetchall()
        return [self._row_to_memory(row) for row in rows]

    def _memories_by_status(self, connection: sqlite3.Connection, statuses: tuple[str, ...]) -> list[Memory]:
        placeholders = ",".join("?" for _ in statuses)
        rows = connection.execute(self._select_sql(f"WHERE status IN ({placeholders})"), statuses).fetchall()
        return [self._row_to_memory(row) for row in rows]

    @staticmethod
    def _set_status(connection: sqlite3.Connection, memory_id: int, status: str, updated_at: str) -> None:
        connection.execute(
            "UPDATE memories SET status = ?, updated_at = ? WHERE id = ?",
            (status, updated_at, memory_id),
        )

    def _mark_used(self, memory_ids: list[int]) -> None:
        placeholders = ",".join("?" for _ in memory_ids)
        with self.connect() as connection:
            connection.execute(
                f"""
                UPDATE memories
                SET last_used_at = ?,
                    use_count = COALESCE(use_count, 0) + 1
                WHERE id IN ({placeholders})
                """,
                (now_iso(), *memory_ids),
            )

    @staticmethod
    def _base_score(memory: Memory) -> float:
        score = memory.priority * 2.0 + memory.confidence + KIND_WEIGHTS.get(memory.kind, 0.4)
        if memory.last_used_at:
            score += 0.1
        return score

    @staticmethod
    def _match_score(memory: Memory, query: str, terms: set[str]) -> float:
        haystack = normalize_memory_text(f"{memory.kind} {memory.content}")
        score = 0.0
        if query.strip() and normalize_memory_text(query) in haystack:
            score += 3.0
        for term in terms:
            if term in haystack:
                score += min(len(term), 8) / 2
        return score

    @staticmethod
    def _select_sql(where_clause: str) -> str:
        return f"""
            SELECT id, kind, content, priority, confidence, source_session_id, source_message_ids,
                   updated_at, last_used_at, use_count, status, sensitivity, expires_at, created_at
            FROM memories
            {where_clause}
        """

    @staticmethod
    def _row_to_memory(row: sqlite3.Row) -> Memory:
        return Memory(
            id=row["id"],
            kind=row["kind"],
            content=row["content"],
            priority=row["priority"],
            confidence=row["confidence"],
            created_at=row["created_at"],
            source_session_id=row["source_session_id"],
            source_message_ids=[int(value) for value in loads_list(row["source_message_ids"]) if _is_int_like(value)],
            updated_at=row["updated_at"],
            last_used_at=row["last_used_at"],
            use_count=int(row["use_count"] or 0),
            status=row["status"],
            sensitivity=row["sensitivity"],
            expires_at=row["expires_at"],
        )


def _query_terms(query: str) -> set[str]:
    normalized = normalize_memory_text(query)
    terms = set(re.findall(r"[a-z0-9_]+|[ぁ-んァ-ヶ一-龯ー]+", normalized))
    if normalized:
        terms.add(normalized)
    return {term for term in terms if term}


def _is_int_like(value: Any) -> bool:
    try:
        int(value)
    except (TypeError, ValueError):
        return False
    return True
