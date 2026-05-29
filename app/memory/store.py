import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.memory.models import DailyReview, DecisionLog, Memory, Message, SessionSummary, Task
from app.memory.repositories.events import EventRepository
from app.memory.repositories.memories import MemoryRepository
from app.memory.repositories.messages import MessageRepository
from app.memory.repositories.reviews import DailyReviewRepository
from app.memory.repositories.summaries import SummaryRepository
from app.memory.repositories.tasks import TaskRepository
from app.memory.repositories.threads import CodexThreadRepository
from app.memory.utils import now_iso, parse_due_at, utc_aware
from app.paths import DB_PATH, REPO_ROOT

__all__ = [
    "DailyReview",
    "DecisionLog",
    "Memory",
    "MemoryStore",
    "Message",
    "SessionSummary",
    "Task",
    "now_iso",
    "parse_due_at",
    "utc_aware",
]


class MemoryStore:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()
        self.messages = MessageRepository(self.connect)
        self.summaries = SummaryRepository(self.connect)
        self.memories = MemoryRepository(self.connect)
        self.tasks = TaskRepository(self.connect)
        self.daily_reviews = DailyReviewRepository(self.connect)
        self.events = EventRepository(self.connect)
        self.codex_threads = CodexThreadRepository(self.connect)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        schema_path = REPO_ROOT / "app" / "memory" / "schema.sql"
        with self.connect() as connection:
            connection.executescript(schema_path.read_text(encoding="utf-8"))

    def add_message(self, session_id: str, role: str, content: str) -> None:
        self.messages.add_message(session_id, role, content)

    def get_recent_messages(self, session_id: str, limit: int = 12) -> list[Message]:
        return self.messages.get_recent_messages(session_id, limit)

    def get_session_messages(self, session_id: str) -> list[Message]:
        return self.messages.get_session_messages(session_id)

    def add_summary(
        self,
        session_id: str,
        summary: str,
        open_loops: list[str],
        decisions: list[str],
        follow_up_candidates: list[str],
    ) -> None:
        self.summaries.add_summary(session_id, summary, open_loops, decisions, follow_up_candidates)

    def list_summaries(self, limit: int = 5) -> list[SessionSummary]:
        return self.summaries.list_summaries(limit)

    def add_memory(self, kind: str, content: str, priority: float = 0.5, confidence: float = 0.8) -> None:
        self.memories.add_memory(kind, content, priority, confidence)

    def memory_exists(self, content: str) -> bool:
        return self.memories.memory_exists(content)

    def list_memories(self, limit: int = 20) -> list[Memory]:
        return self.memories.list_memories(limit)

    def search_memories(self, query: str, limit: int = 6) -> list[Memory]:
        return self.memories.search_memories(query, limit)

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
        return self.tasks.list_due_tasks(now, limit)

    def add_task(
        self,
        title: str,
        source: str,
        source_session_id: str | None = None,
        description: str | None = None,
        priority: float = 0.5,
        due_at: str | None = None,
    ) -> int | None:
        return self.tasks.add_task(title, source, source_session_id, description, priority, due_at)

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
        return self.tasks.task_exists(title)

    def task_titles(self) -> set[str]:
        return self.tasks.task_titles()

    def list_tasks(self, statuses: tuple[str, ...] | None = None, limit: int = 20) -> list[Task]:
        return self.tasks.list_tasks(statuses, limit)

    def mark_task_done(self, task_id: int) -> bool:
        return self.tasks.mark_task_done(task_id)

    def snooze_task(self, task_id: int, due_at: str) -> bool:
        return self.tasks.snooze_task(task_id, due_at)

    def _update_task_status(
        self,
        task_id: int,
        status: str,
        due_at: str | None = None,
        allowed_statuses: tuple[str, ...] | None = None,
    ) -> bool:
        return self.tasks.update_task_status(task_id, status, due_at, allowed_statuses)

    def add_daily_review(self, review_date: str, summary: str, items_json: str) -> int:
        return self.daily_reviews.add_daily_review(review_date, summary, items_json)

    def recent_daily_reviews(self, limit: int = 5) -> list[DailyReview]:
        return self.daily_reviews.recent_daily_reviews(limit)

    def add_proactive_event(
        self,
        proposed_text: str,
        outcome: str | None = None,
        user_response: str | None = None,
        memory_id: int | None = None,
    ) -> None:
        self.events.add_proactive_event(proposed_text, outcome, user_response, memory_id)

    def add_decision_log(
        self,
        kind: str,
        decision: str,
        reason: str,
        session_id: str | None = None,
        task_id: int | None = None,
        candidate_text: str | None = None,
        score: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.events.add_decision_log(kind, decision, reason, session_id, task_id, candidate_text, score, metadata)

    def get_codex_thread_id(self, session_id: str) -> str | None:
        return self.codex_threads.get_codex_thread_id(session_id)

    def set_codex_thread_id(self, session_id: str, codex_thread_id: str) -> None:
        self.codex_threads.set_codex_thread_id(session_id, codex_thread_id)

    def recent_proactive_events(self, limit: int = 10) -> list[dict[str, Any]]:
        return self.events.recent_proactive_events(limit)

    def recent_decision_logs(self, limit: int = 20) -> list[DecisionLog]:
        return self.events.recent_decision_logs(limit)
