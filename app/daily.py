import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.memory.store import MemoryStore, SessionSummary, Task


@dataclass(frozen=True)
class DailyReviewItem:
    source: str
    title: str
    reason: str
    id: int | str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "id": self.id,
            "title": self.title,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class DailyReviewPlan:
    review_date: str
    summary: str
    items: list[DailyReviewItem]
    open_tasks: list[Task]
    snoozed_tasks: list[Task]
    recent_summaries: list[SessionSummary]
    open_loops: list[str]
    follow_up_candidates: list[str]

    def items_json(self) -> str:
        return json.dumps([item.to_dict() for item in self.items], ensure_ascii=False)


class DailyReviewService:
    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def build(self, review_date: str | None = None) -> DailyReviewPlan:
        date = review_date or datetime.now().astimezone().date().isoformat()
        open_tasks = self.store.list_tasks(statuses=("open",), limit=20)
        snoozed_tasks = self.store.list_tasks(statuses=("snoozed",), limit=20)
        recent_summaries = self.store.list_summaries(limit=5)
        open_loops = self._unique(
            loop for summary in recent_summaries for loop in summary.open_loops
        )
        follow_up_candidates = self._unique(
            follow_up for summary in recent_summaries for follow_up in summary.follow_up_candidates
        )
        items = self._build_items(
            open_tasks,
            snoozed_tasks,
            open_loops,
            follow_up_candidates,
            known_task_titles=self.store.task_titles(),
        )
        summary = self._build_summary(items, open_tasks, snoozed_tasks, recent_summaries)
        return DailyReviewPlan(
            review_date=date,
            summary=summary,
            items=items,
            open_tasks=open_tasks,
            snoozed_tasks=snoozed_tasks,
            recent_summaries=recent_summaries,
            open_loops=open_loops,
            follow_up_candidates=follow_up_candidates,
        )

    def build_and_save(self, review_date: str | None = None) -> DailyReviewPlan:
        plan = self.build(review_date)
        self.store.add_daily_review(
            review_date=plan.review_date,
            summary=plan.summary,
            items_json=plan.items_json(),
        )
        return plan

    def _build_items(
        self,
        open_tasks: list[Task],
        snoozed_tasks: list[Task],
        open_loops: list[str],
        follow_up_candidates: list[str],
        known_task_titles: set[str],
    ) -> list[DailyReviewItem]:
        items: list[DailyReviewItem] = []
        seen_titles: set[str] = set(known_task_titles)

        for task in open_tasks:
            reason = "open task"
            if task.due_at:
                reason = f"open task due {task.due_at}"
            items.append(DailyReviewItem(source="task", id=task.id, title=task.title, reason=reason))
            seen_titles.add(task.title)

        for task in snoozed_tasks:
            reason = "snoozed task"
            if task.due_at:
                reason = f"snoozed until {task.due_at}"
            items.append(DailyReviewItem(source="snoozed", id=task.id, title=task.title, reason=reason))
            seen_titles.add(task.title)

        for title in open_loops:
            if title in seen_titles:
                continue
            items.append(DailyReviewItem(source="open_loop", title=title, reason="recent open loop"))
            seen_titles.add(title)

        for title in follow_up_candidates:
            if title in seen_titles:
                continue
            items.append(
                DailyReviewItem(source="follow_up_candidate", title=title, reason="recent follow-up candidate")
            )
            seen_titles.add(title)

        return items

    @staticmethod
    def _build_summary(
        items: list[DailyReviewItem],
        open_tasks: list[Task],
        snoozed_tasks: list[Task],
        recent_summaries: list[SessionSummary],
    ) -> str:
        if not items:
            return "今日の確認候補はありません。"
        return (
            "今日の確認候補: "
            f"{len(items)}件 "
            f"(open tasks {len(open_tasks)}, snoozed tasks {len(snoozed_tasks)}, "
            f"recent summaries {len(recent_summaries)})"
        )

    @staticmethod
    def _unique(values: Iterable[str]) -> list[str]:
        unique_values: list[str] = []
        seen: set[str] = set()
        for value in values:
            title = str(value).strip()
            if not title or title in seen:
                continue
            unique_values.append(title)
            seen.add(title)
        return unique_values
