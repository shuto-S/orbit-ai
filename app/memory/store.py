import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.memory.models import (
    ApprovalRequest,
    DailyReview,
    DecisionLog,
    Draft,
    Memory,
    Message,
    OpenLoop,
    SessionSummary,
    Task,
)
from app.memory.repositories.approval_requests import ApprovalRequestRepository
from app.memory.repositories.drafts import DraftRepository
from app.memory.repositories.events import EventRepository
from app.memory.repositories.memories import MemoryRepository
from app.memory.repositories.messages import MessageRepository
from app.memory.repositories.open_loops import OpenLoopRepository
from app.memory.repositories.reviews import DailyReviewRepository
from app.memory.repositories.summaries import SummaryRepository
from app.memory.repositories.tasks import TaskRepository
from app.memory.repositories.threads import CodexThreadRepository
from app.memory.utils import now_iso, parse_due_at, utc_aware
from app.paths import DB_PATH, REPO_ROOT

__all__ = [
    "DailyReview",
    "ApprovalRequest",
    "DecisionLog",
    "Draft",
    "Memory",
    "MemoryStore",
    "Message",
    "OpenLoop",
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
        self.open_loops = OpenLoopRepository(self.connect)
        self.daily_reviews = DailyReviewRepository(self.connect)
        self.approval_requests = ApprovalRequestRepository(self.connect)
        self.drafts = DraftRepository(self.connect)
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
            self._ensure_memory_schema_compat(connection)

    @staticmethod
    def _ensure_memory_schema_compat(connection: sqlite3.Connection) -> None:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(memories)").fetchall()}
        column_sql = {
            "source_session_id": "ALTER TABLE memories ADD COLUMN source_session_id TEXT",
            "source_message_ids": "ALTER TABLE memories ADD COLUMN source_message_ids TEXT",
            "updated_at": "ALTER TABLE memories ADD COLUMN updated_at TEXT",
            "use_count": "ALTER TABLE memories ADD COLUMN use_count INTEGER DEFAULT 0",
            "status": "ALTER TABLE memories ADD COLUMN status TEXT NOT NULL DEFAULT 'active'",
            "sensitivity": "ALTER TABLE memories ADD COLUMN sensitivity TEXT NOT NULL DEFAULT 'normal'",
            "expires_at": "ALTER TABLE memories ADD COLUMN expires_at TEXT",
        }
        for column, statement in column_sql.items():
            if column not in columns:
                connection.execute(statement)
        connection.execute(
            "UPDATE memories SET updated_at = created_at WHERE updated_at IS NULL OR updated_at = ''"
        )
        connection.execute("UPDATE memories SET status = 'active' WHERE status IS NULL OR status = ''")
        connection.execute("UPDATE memories SET sensitivity = 'normal' WHERE sensitivity IS NULL OR sensitivity = ''")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_messages_session_id_id ON messages(session_id, id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_memories_status_kind ON memories(status, kind)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_memories_updated_at ON memories(updated_at)")

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
    ) -> int | None:
        return self.memories.add_memory(
            kind,
            content,
            priority,
            confidence,
            source_session_id=source_session_id,
            source_message_ids=source_message_ids,
            sensitivity=sensitivity,
            expires_at=expires_at,
        )

    def memory_exists(self, content: str) -> bool:
        return self.memories.memory_exists(content)

    def get_memory(self, memory_id: int) -> Memory | None:
        return self.memories.get_memory(memory_id)

    def list_memories(self, limit: int = 20, statuses: tuple[str, ...] = ("active",)) -> list[Memory]:
        return self.memories.list_memories(limit, statuses)

    def search_memories(self, query: str, limit: int = 6) -> list[Memory]:
        return self.memories.search_memories(query, limit)

    def forget_memory(self, memory_id: int) -> bool:
        return self.memories.forget_memory(memory_id)

    def archive_memory(self, memory_id: int) -> bool:
        return self.memories.archive_memory(memory_id)

    def latest_open_loops(self, limit: int = 5) -> list[str]:
        return self.list_open_task_titles_for_proactive(datetime.now(UTC), limit=limit)

    def list_open_task_titles_for_proactive(self, now: datetime, limit: int = 5) -> list[str]:
        loops: list[str] = []
        selected_titles: set[str] = set()
        for task in self.list_due_tasks(now, limit=limit):
            loops.append(task.title)
            selected_titles.add(task.title)
            if len(loops) >= limit:
                return loops[:limit]
        for task in self.list_tasks(statuses=("open",), limit=limit):
            if task.title in selected_titles:
                continue
            loops.append(task.title)
            selected_titles.add(task.title)
            if len(loops) >= limit:
                return loops[:limit]
        known_task_titles = self.task_titles()
        for loop in self.list_open_loops(statuses=("open",), limit=limit):
            if loop.title in selected_titles or loop.title in known_task_titles:
                continue
            loops.append(loop.title)
            selected_titles.add(loop.title)
            if len(loops) >= limit:
                return loops[:limit]
        for summary in self.list_summaries(limit=20):
            for title in [*summary.open_loops, *summary.follow_up_candidates]:
                if title in selected_titles or title in known_task_titles:
                    continue
                loops.append(title)
                selected_titles.add(title)
                if len(loops) >= limit:
                    return loops[:limit]
        return loops[:limit]

    def add_open_loop(
        self,
        title: str,
        summary: str | None = None,
        source_session_id: str | None = None,
        source_message_id: int | None = None,
        suggested_next_step: str | None = None,
        importance: float = 0.5,
        confidence: float = 0.5,
        due_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int | None:
        return self.open_loops.add_open_loop(
            title=title,
            summary=summary,
            source_session_id=source_session_id,
            source_message_id=source_message_id,
            suggested_next_step=suggested_next_step,
            importance=importance,
            confidence=confidence,
            due_at=due_at,
            metadata=metadata,
        )

    def list_open_loops(self, statuses: tuple[str, ...] = ("open",), limit: int = 20) -> list[OpenLoop]:
        return self.open_loops.list_open_loops(statuses=statuses, limit=limit)

    def latest_resume_point(self) -> OpenLoop | None:
        loops = self.list_open_loops(statuses=("open",), limit=20)
        if not loops:
            return None
        for loop in loops:
            if loop.suggested_next_step or loop.metadata.get("kind") == "next_resume_point":
                return loop
        return loops[0]

    def get_open_loop(self, loop_id: int) -> OpenLoop | None:
        return self.open_loops.get_open_loop(loop_id)

    def update_open_loop_status(self, loop_id: int, status: str) -> bool:
        return self.open_loops.update_open_loop_status(loop_id, status)

    def resolve_open_loop(self, loop_id: int) -> bool:
        return self.open_loops.resolve_open_loop(loop_id)

    def archive_open_loop(self, loop_id: int) -> bool:
        return self.open_loops.archive_open_loop(loop_id)

    def touch_open_loop(self, loop_id: int, now: datetime | None = None) -> bool:
        return self.open_loops.touch_open_loop(loop_id, now)

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
            self.add_open_loop(
                title=title,
                summary=title,
                source_session_id=session_id,
                metadata={"source": "session_summary"},
            )
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
        return self.approval_requests.add_approval_request(
            action=action,
            payload=payload,
            reason=reason,
            risk_level=risk_level,
            source_session_id=source_session_id,
            source_message_id=source_message_id,
            expires_at=expires_at,
            metadata=metadata,
        )

    def list_approval_requests(self, status: str = "pending", limit: int = 20) -> list[ApprovalRequest]:
        return self.approval_requests.list_approval_requests(status, limit)

    def get_approval_request(self, request_id: int) -> ApprovalRequest | None:
        return self.approval_requests.get_approval_request(request_id)

    def approve_request(self, request_id: int) -> ApprovalRequest | None:
        return self.approval_requests.approve_request(request_id)

    def reject_request(self, request_id: int) -> ApprovalRequest | None:
        return self.approval_requests.reject_request(request_id)

    def add_draft(
        self,
        kind: str,
        title: str,
        body: str,
        source_session_id: str | None = None,
        source_message_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int | None:
        return self.drafts.add_draft(
            kind=kind,
            title=title,
            body=body,
            source_session_id=source_session_id,
            source_message_id=source_message_id,
            metadata=metadata,
        )

    def list_drafts(self, status: str = "draft", limit: int = 20) -> list[Draft]:
        return self.drafts.list_drafts(status, limit)

    def get_draft(self, draft_id: int) -> Draft | None:
        return self.drafts.get_draft(draft_id)

    def update_draft_status(self, draft_id: int, status: str) -> Draft | None:
        return self.drafts.update_draft_status(draft_id, status)

    def archive_draft(self, draft_id: int) -> Draft | None:
        return self.drafts.archive_draft(draft_id)

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
