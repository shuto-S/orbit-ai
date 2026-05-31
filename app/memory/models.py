from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Message:
    session_id: str
    role: str
    content: str
    created_at: str
    id: int | None = None


@dataclass(frozen=True)
class Memory:
    id: int
    kind: str
    content: str
    priority: float
    confidence: float
    created_at: str
    source_session_id: str | None = None
    source_message_ids: list[int] = field(default_factory=list)
    updated_at: str | None = None
    last_used_at: str | None = None
    use_count: int = 0
    status: str = "active"
    sensitivity: str = "normal"
    expires_at: str | None = None


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


@dataclass(frozen=True)
class DailyReview:
    id: int
    review_date: str
    summary: str
    items: list[dict[str, Any]]
    created_at: str


@dataclass(frozen=True)
class DecisionLog:
    id: int
    kind: str
    session_id: str | None
    task_id: int | None
    candidate_text: str | None
    decision: str
    reason: str
    score: float | None
    metadata_json: str | None
    created_at: str
