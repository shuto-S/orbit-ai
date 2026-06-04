from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from string import Template
from typing import Any

from app.ai.backends.base import LlmBackend, LlmBackendError
from app.memory.merger import is_sensitive_text
from app.paths import PROMPTS_DIR
from app.text import sanitize_text

ALLOWED_MEMORY_KINDS = {
    "preference",
    "profile",
    "project",
    "decision",
    "open_loop",
    "relationship",
    "manual",
}


@dataclass(frozen=True)
class TaskCandidate:
    title: str
    due_text: str | None
    confidence: float
    needs_confirmation: bool
    source_text: str


@dataclass(frozen=True)
class MemoryCandidate:
    content: str
    kind: str
    confidence: float
    sensitivity: str
    needs_confirmation: bool
    source_text: str


@dataclass(frozen=True)
class OpenLoopCandidate:
    title: str
    summary: str
    suggested_next_step: str | None
    confidence: float
    source_text: str


@dataclass(frozen=True)
class FollowUpCandidate:
    text: str
    due_text: str | None
    reason: str
    confidence: float


@dataclass(frozen=True)
class TurnAnalysis:
    task_candidates: list[TaskCandidate] = field(default_factory=list)
    memory_candidates: list[MemoryCandidate] = field(default_factory=list)
    open_loop_candidates: list[OpenLoopCandidate] = field(default_factory=list)
    follow_up_candidates: list[FollowUpCandidate] = field(default_factory=list)
    permission_required_actions: list[dict[str, Any]] = field(default_factory=list)
    status: str = "ok"
    failure_reason: str | None = None

    @classmethod
    def empty(cls, status: str = "empty", failure_reason: str | None = None) -> TurnAnalysis:
        return cls(status=status, failure_reason=failure_reason)

    def has_candidates(self) -> bool:
        return bool(
            self.task_candidates
            or self.memory_candidates
            or self.open_loop_candidates
            or self.follow_up_candidates
            or self.permission_required_actions
        )

    def max_confidence(self) -> float | None:
        values = [
            candidate.confidence
            for candidate in [
                *self.task_candidates,
                *self.memory_candidates,
                *self.open_loop_candidates,
                *self.follow_up_candidates,
            ]
        ]
        return max(values) if values else None

    def to_metadata(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "failure_reason": self.failure_reason,
            "task_candidates": [asdict(candidate) for candidate in self.task_candidates],
            "memory_candidates": [asdict(candidate) for candidate in self.memory_candidates],
            "open_loop_candidates": [asdict(candidate) for candidate in self.open_loop_candidates],
            "follow_up_candidates": [asdict(candidate) for candidate in self.follow_up_candidates],
            "permission_required_actions": self.permission_required_actions,
        }


class TurnAnalysisAgent:
    def __init__(self, backend: LlmBackend | None = None, timeout_seconds: int = 45) -> None:
        self.backend = backend
        self.timeout_seconds = timeout_seconds

    def analyze(self, user_text: str, assistant_text: str) -> TurnAnalysis:
        if self.backend is None:
            return TurnAnalysis.empty(status="disabled", failure_reason="backend unavailable")

        try:
            response = self.backend.ask(
                self._build_prompt(user_text, assistant_text),
                timeout=self.timeout_seconds,
            )
        except LlmBackendError as exc:
            return TurnAnalysis.empty(status="backend_failure", failure_reason=sanitize_text(str(exc)))

        payload = _parse_json_object(response.text)
        if payload is None:
            return TurnAnalysis.empty(status="invalid_json", failure_reason="response was not a JSON object")

        return TurnAnalysis(
            task_candidates=_task_candidates(payload.get("task_candidates")),
            memory_candidates=_memory_candidates(payload.get("memory_candidates")),
            open_loop_candidates=_open_loop_candidates(payload.get("open_loop_candidates")),
            follow_up_candidates=_follow_up_candidates(payload.get("follow_up_candidates")),
            permission_required_actions=_permission_actions(payload.get("permission_required_actions")),
        )

    def _build_prompt(self, user_text: str, assistant_text: str) -> str:
        template = (PROMPTS_DIR / "turn_analysis.md").read_text(encoding="utf-8")
        return Template(template).safe_substitute(
            {
                "user_text": sanitize_text(user_text).strip() or "なし",
                "assistant_text": sanitize_text(assistant_text).strip() or "なし",
            }
        )


def _task_candidates(value: Any) -> list[TaskCandidate]:
    candidates: list[TaskCandidate] = []
    for item in _items(value):
        title = _optional_text(item.get("title"))
        source_text = _optional_text(item.get("source_text")) or title
        if not title or _contains_sensitive([title, source_text, item.get("due_text")]):
            continue
        candidates.append(
            TaskCandidate(
                title=title,
                due_text=_optional_text(item.get("due_text")),
                confidence=_bounded_float(item.get("confidence"), default=0.5),
                needs_confirmation=_bool(item.get("needs_confirmation"), default=True),
                source_text=source_text,
            )
        )
    return candidates


def _memory_candidates(value: Any) -> list[MemoryCandidate]:
    candidates: list[MemoryCandidate] = []
    for item in _items(value):
        content = _optional_text(item.get("content"))
        source_text = _optional_text(item.get("source_text")) or content
        sensitivity = str(item.get("sensitivity", "normal")).strip().lower()
        if sensitivity != "normal" or not content or _contains_sensitive([content, source_text]):
            continue
        kind = str(item.get("kind", "project")).strip().lower()
        if kind not in ALLOWED_MEMORY_KINDS:
            kind = "project"
        candidates.append(
            MemoryCandidate(
                content=content,
                kind=kind,
                confidence=_bounded_float(item.get("confidence"), default=0.5),
                sensitivity="normal",
                needs_confirmation=_bool(item.get("needs_confirmation"), default=True),
                source_text=source_text,
            )
        )
    return candidates


def _open_loop_candidates(value: Any) -> list[OpenLoopCandidate]:
    candidates: list[OpenLoopCandidate] = []
    for item in _items(value):
        title = _optional_text(item.get("title"))
        summary = _optional_text(item.get("summary"))
        source_text = _optional_text(item.get("source_text")) or summary or title
        suggested_next_step = _optional_text(item.get("suggested_next_step"))
        if not title or not summary or _contains_sensitive([title, summary, source_text, suggested_next_step]):
            continue
        candidates.append(
            OpenLoopCandidate(
                title=title,
                summary=summary,
                suggested_next_step=suggested_next_step,
                confidence=_bounded_float(item.get("confidence"), default=0.5),
                source_text=source_text,
            )
        )
    return candidates


def _follow_up_candidates(value: Any) -> list[FollowUpCandidate]:
    candidates: list[FollowUpCandidate] = []
    for item in _items(value):
        text = _optional_text(item.get("text"))
        reason = _optional_text(item.get("reason")) or ""
        if not text or _contains_sensitive([text, reason, item.get("due_text")]):
            continue
        candidates.append(
            FollowUpCandidate(
                text=text,
                due_text=_optional_text(item.get("due_text")),
                reason=reason,
                confidence=_bounded_float(item.get("confidence"), default=0.5),
            )
        )
    return candidates


def _permission_actions(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in _items(value):
        if _contains_sensitive(item):
            continue
        sanitized = _sanitize_json_value(item)
        if isinstance(sanitized, dict):
            result.append(sanitized)
    return result


def _items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _parse_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _bounded_float(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, number))


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = sanitize_text(str(value)).strip()
    return text or None


def _contains_sensitive(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return is_sensitive_text(value)
    if isinstance(value, list):
        return any(_contains_sensitive(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_sensitive(key) or _contains_sensitive(item) for key, item in value.items())
    return False


def _sanitize_json_value(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return sanitize_text(value).strip()
    if isinstance(value, list):
        return [_sanitize_json_value(item) for item in value]
    if isinstance(value, dict):
        return {
            sanitize_text(str(key)).strip(): _sanitize_json_value(item)
            for key, item in value.items()
            if sanitize_text(str(key)).strip()
        }
    return sanitize_text(str(value)).strip()
