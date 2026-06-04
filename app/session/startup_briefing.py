from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime

from app.memory.merger import is_sensitive_text
from app.memory.store import MemoryStore, Task, parse_due_at
from app.text import sanitize_text

MAX_BRIEFING_ITEMS = 3
BRIEFING_SCAN_LIMIT = MAX_BRIEFING_ITEMS + 1


@dataclass(frozen=True)
class StartupBriefing:
    text: str
    reason: str
    suggested_actions: list[str]


@dataclass(frozen=True)
class _BriefingItem:
    title: str
    source: str
    priority: float = 0.5
    due_at: str | None = None
    summary: str | None = None
    suggested_next_step: str | None = None


class StartupBriefingService:
    def build(self, store: MemoryStore, now: datetime | None = None) -> StartupBriefing | None:
        now = _utc_now(now)
        try:
            candidates = self._candidates(store, now)
        except sqlite3.OperationalError:
            return None
        if not candidates:
            return None
        items = candidates[:MAX_BRIEFING_ITEMS]
        reason = items[0].source
        return StartupBriefing(
            text=self._format_text(items, len(candidates), reason),
            reason=reason,
            suggested_actions=self._suggested_actions(items, store, now),
        )

    def _candidates(self, store: MemoryStore, now: datetime) -> list[_BriefingItem]:
        selected_titles: set[str] = set()
        candidates: list[_BriefingItem] = []

        for task in store.list_due_tasks(now, limit=BRIEFING_SCAN_LIMIT):
            _append_task(candidates, selected_titles, task, source="due_task")
        if len(candidates) >= BRIEFING_SCAN_LIMIT:
            return candidates

        open_tasks = store.list_tasks(statuses=("open",), limit=100)
        due_open_tasks = [task for task in open_tasks if parse_due_at(task.due_at) is not None]
        due_open_tasks.sort(
            key=lambda task: (parse_due_at(task.due_at) or datetime.max.replace(tzinfo=UTC), -task.priority)
        )
        for task in due_open_tasks:
            _append_task(candidates, selected_titles, task, source="open_task_due")
            if len(candidates) >= BRIEFING_SCAN_LIMIT:
                return candidates

        for task in open_tasks:
            _append_task(candidates, selected_titles, task, source="open_task")
            if len(candidates) >= BRIEFING_SCAN_LIMIT:
                return candidates

        known_task_titles = store.task_titles()
        for loop in store.list_open_loops(statuses=("open",), limit=BRIEFING_SCAN_LIMIT):
            if loop.title in known_task_titles:
                continue
            _append_item(
                candidates,
                selected_titles,
                _BriefingItem(
                    title=loop.title,
                    source="open_loop",
                    priority=loop.importance,
                    summary=loop.summary,
                    suggested_next_step=loop.suggested_next_step,
                ),
            )
            if len(candidates) >= BRIEFING_SCAN_LIMIT:
                return candidates

        for summary in store.list_summaries(limit=5):
            for title in [*summary.open_loops, *summary.follow_up_candidates]:
                if title in known_task_titles:
                    continue
                _append_item(
                    candidates,
                    selected_titles,
                    _BriefingItem(title=title, source="summary_open_loop", summary=summary.summary),
                )
                if len(candidates) >= BRIEFING_SCAN_LIMIT:
                    return candidates

        return candidates

    def _format_text(self, items: list[_BriefingItem], total_count: int, reason: str) -> str:
        count_text = f"{total_count}件" if total_count <= MAX_BRIEFING_ITEMS else f"{MAX_BRIEFING_ITEMS}件以上"
        if reason == "due_task":
            opening = f"おはようございます。期限が来ている未完了項目が{count_text}あります。"
        elif reason == "open_loop":
            opening = f"おはようございます。前回から残っている未解決トピックが{count_text}あります。"
        else:
            opening = f"おはようございます。未完了の項目が{count_text}あります。"

        item_text = "、".join(f"「{item.title}」" for item in items)
        top_item = items[0]
        if len(items) == 1:
            middle = f"優先度が高そうなのは{item_text}です。"
        else:
            middle = f"優先度が高そうなのは{item_text}です。"
        next_step = top_item.suggested_next_step or f"{top_item.title}の続き"
        question = f"まずは「{next_step}」から始めますか？"
        return "\n".join([opening, middle, question])

    def _suggested_actions(self, items: list[_BriefingItem], store: MemoryStore, now: datetime) -> list[str]:
        actions = [f"resume:{items[0].title}"]
        if not _has_daily_review_today(store, now.date()):
            actions.append("daily_review")
        return actions


def _append_task(
    candidates: list[_BriefingItem],
    selected_titles: set[str],
    task: Task,
    source: str,
) -> None:
    _append_item(
        candidates,
        selected_titles,
        _BriefingItem(
            title=task.title,
            source=source,
            priority=task.priority,
            due_at=task.due_at,
            summary=task.description,
        ),
    )


def _append_item(
    candidates: list[_BriefingItem],
    selected_titles: set[str],
    item: _BriefingItem,
) -> None:
    title = sanitize_text(item.title).strip()
    if not title or title in selected_titles or is_sensitive_text(title):
        return
    summary = sanitize_text(item.summary).strip() if item.summary else None
    suggested_next_step = sanitize_text(item.suggested_next_step).strip() if item.suggested_next_step else None
    if summary and is_sensitive_text(summary):
        summary = None
    if suggested_next_step and is_sensitive_text(suggested_next_step):
        suggested_next_step = None
    candidates.append(
        _BriefingItem(
            title=title,
            source=item.source,
            priority=item.priority,
            due_at=item.due_at,
            summary=summary,
            suggested_next_step=suggested_next_step,
        )
    )
    selected_titles.add(title)


def _has_daily_review_today(store: MemoryStore, today: date) -> bool:
    return any(review.review_date == today.isoformat() for review in store.recent_daily_reviews(limit=5))


def _utc_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    if now.tzinfo is None:
        return now.replace(tzinfo=UTC)
    return now
