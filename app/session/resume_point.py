from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.memory.merger import is_sensitive_text
from app.memory.store import Message
from app.text import sanitize_text

ACTIONABLE_KEYWORDS = (
    "実装",
    "設計",
    "検討",
    "確認",
    "整理",
    "続き",
    "未定",
    "課題",
    "方針",
    "あとで",
    "後で",
)
CASUAL_CLOSE_KEYWORDS = (
    "ありがとう",
    "ここまで",
    "終わり",
    "終了",
    "またね",
)
MAX_RESUME_POINT_CHARS = 60


@dataclass(frozen=True)
class SessionResumePoint:
    title: str
    suggested_next_action: str
    reason: str


def build_session_resume_point(
    messages: list[Message],
    summary: dict[str, Any],
) -> SessionResumePoint | None:
    open_loops = _string_list(summary.get("open_loops"))
    follow_ups = _string_list(summary.get("follow_up_candidates"))

    for title in [*open_loops, *follow_ups]:
        point = _point_from_text(title, reason="summary_open_loop", follow_ups=follow_ups)
        if point is not None:
            return point

    for message in reversed(messages):
        if message.role != "user":
            continue
        point = _point_from_text(message.content, reason="recent_message_fallback", follow_ups=follow_ups)
        if point is not None:
            return point
    return None


def _point_from_text(text: str, reason: str, follow_ups: list[str]) -> SessionResumePoint | None:
    title = _resume_title(text)
    if title is None:
        return None
    next_action = _suggested_next_action(title, follow_ups)
    return SessionResumePoint(title=title, suggested_next_action=next_action, reason=reason)


def _resume_title(text: str) -> str | None:
    title = sanitize_text(text).strip()
    if not title or is_sensitive_text(title):
        return None
    if any(keyword in title for keyword in CASUAL_CLOSE_KEYWORDS) and not any(
        keyword in title for keyword in ACTIONABLE_KEYWORDS
    ):
        return None
    if not any(keyword in title for keyword in ACTIONABLE_KEYWORDS):
        return None
    if len(title) > MAX_RESUME_POINT_CHARS:
        title = title[: MAX_RESUME_POINT_CHARS - 3].rstrip() + "..."
    return title


def _suggested_next_action(title: str, follow_ups: list[str]) -> str:
    for follow_up in follow_ups:
        candidate = sanitize_text(follow_up).strip()
        if candidate and candidate != title and not is_sensitive_text(candidate):
            return candidate
    return f"{title}の次の一手を決める"


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [sanitize_text(str(item)).strip() for item in value if sanitize_text(str(item)).strip()]
