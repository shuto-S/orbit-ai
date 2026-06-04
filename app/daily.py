import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.memory.store import Memory, MemoryStore, OpenLoop, SessionSummary, Task

DAILY_CONTEXT_MEMORY_KINDS = {"project", "decision", "manual"}
DAILY_CONTEXT_MIN_PRIORITY = 0.75
DAILY_CONTEXT_TEXT_LIMIT = 60


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
    due_tasks: list[Task]
    open_tasks: list[Task]
    snoozed_tasks: list[Task]
    recent_summaries: list[SessionSummary]
    open_loop_records: list[OpenLoop]
    context_memories: list[Memory]
    open_loops: list[str]
    follow_up_candidates: list[str]
    suggested_actions: list[str]
    confirmation_questions: list[str]

    def items_json(self) -> str:
        return json.dumps([item.to_dict() for item in self.items], ensure_ascii=False)


class DailyReviewService:
    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def build(self, review_date: str | None = None, now: datetime | None = None) -> DailyReviewPlan:
        now = _aware_now(now)
        date = review_date or now.astimezone().date().isoformat()
        due_tasks = self.store.list_due_tasks(now, limit=20)
        open_tasks = self.store.list_tasks(statuses=("open",), limit=20)
        snoozed_tasks = self.store.list_tasks(statuses=("snoozed",), limit=20)
        recent_summaries = self.store.list_summaries(limit=5)
        open_loop_records = self.store.list_open_loops(statuses=("open",), limit=20)
        context_memories = self._context_memories()
        open_loops = self._unique(
            [
                *(loop.title for loop in open_loop_records),
                *(loop for summary in recent_summaries for loop in summary.open_loops),
            ]
        )
        follow_up_candidates = self._unique(
            follow_up for summary in recent_summaries for follow_up in summary.follow_up_candidates
        )
        known_task_titles = self.store.task_titles()
        items = self._build_items(
            due_tasks,
            open_tasks,
            snoozed_tasks,
            open_loop_records,
            open_loops,
            follow_up_candidates,
            known_task_titles=known_task_titles,
        )
        suggested_actions = self._suggested_actions(items, open_loop_records, context_memories, known_task_titles)
        confirmation_questions = self._confirmation_questions(items)
        summary = self._build_summary(items, suggested_actions, confirmation_questions)
        return DailyReviewPlan(
            review_date=date,
            summary=summary,
            items=items,
            due_tasks=due_tasks,
            open_tasks=open_tasks,
            snoozed_tasks=snoozed_tasks,
            recent_summaries=recent_summaries,
            open_loop_records=open_loop_records,
            context_memories=context_memories,
            open_loops=open_loops,
            follow_up_candidates=follow_up_candidates,
            suggested_actions=suggested_actions,
            confirmation_questions=confirmation_questions,
        )

    def build_and_save(self, review_date: str | None = None, now: datetime | None = None) -> DailyReviewPlan:
        plan = self.build(review_date, now)
        self.store.add_daily_review(
            review_date=plan.review_date,
            summary=plan.summary,
            items_json=plan.items_json(),
        )
        return plan

    def _build_items(
        self,
        due_tasks: list[Task],
        open_tasks: list[Task],
        snoozed_tasks: list[Task],
        open_loop_records: list[OpenLoop],
        open_loops: list[str],
        follow_up_candidates: list[str],
        known_task_titles: set[str],
    ) -> list[DailyReviewItem]:
        items: list[DailyReviewItem] = []
        seen_titles: set[str] = set()
        seen_task_ids: set[int] = set()

        for task in due_tasks:
            items.append(
                DailyReviewItem(source="due_task", id=task.id, title=task.title, reason=f"due {task.due_at}")
            )
            seen_titles.add(task.title)
            seen_task_ids.add(task.id)

        for task in open_tasks:
            reason = "open task"
            if task.due_at:
                reason = f"open task due {task.due_at}"
            items.append(DailyReviewItem(source="task", id=task.id, title=task.title, reason=reason))
            seen_titles.add(task.title)
            seen_task_ids.add(task.id)

        for task in snoozed_tasks:
            if task.id in seen_task_ids:
                continue
            reason = "snoozed task"
            if task.due_at:
                reason = f"snoozed until {task.due_at}"
            items.append(DailyReviewItem(source="snoozed", id=task.id, title=task.title, reason=reason))
            seen_titles.add(task.title)
            seen_task_ids.add(task.id)

        open_loop_by_title = {loop.title: loop for loop in open_loop_records}
        for title in open_loops:
            if title in seen_titles:
                continue
            loop = open_loop_by_title.get(title)
            if loop is None and title in known_task_titles:
                continue
            reason = loop.suggested_next_step if loop and loop.suggested_next_step else "open loop"
            item_id = loop.id if loop is not None else None
            items.append(DailyReviewItem(source="open_loop", id=item_id, title=title, reason=reason))
            seen_titles.add(title)

        for title in follow_up_candidates:
            if title in seen_titles or title in known_task_titles:
                continue
            items.append(
                DailyReviewItem(source="follow_up_candidate", title=title, reason="recent follow-up candidate")
            )
            seen_titles.add(title)

        return items

    @staticmethod
    def _build_summary(
        items: list[DailyReviewItem],
        suggested_actions: list[str],
        confirmation_questions: list[str],
    ) -> str:
        if not items and not suggested_actions and not confirmation_questions:
            return "今日の未完了項目は見つかりません。新しく整理したいことがあれば話してください。"

        lines = ["今日の整理です。", "", "未完了:"]
        if items:
            for item in items[:3]:
                lines.append(f"- {item.title}")
        else:
            lines.append("- なし")
        if suggested_actions:
            lines.extend(["", "今日やると良さそう:"])
            for action in suggested_actions[:3]:
                lines.append(f"- {action}")
        if confirmation_questions:
            lines.extend(["", "確認したいこと:"])
            for question in confirmation_questions[:3]:
                lines.append(f"- {question}")
        return "\n".join(lines)

    @staticmethod
    def _suggested_actions(
        items: list[DailyReviewItem],
        open_loop_records: list[OpenLoop],
        context_memories: list[Memory],
        known_task_titles: set[str],
    ) -> list[str]:
        actions: list[str] = []
        item_titles = {item.title for item in items}
        open_loop_next_steps = {
            loop.title: loop.suggested_next_step for loop in open_loop_records if loop.suggested_next_step
        }
        for item in items:
            if item.source == "due_task":
                action = f"{item.title}を今日確認する"
            elif item.source == "task" and item.reason.startswith("open task due"):
                action = f"{item.title}を期限に合わせて進める"
            elif item.source == "open_loop" and item.title in open_loop_next_steps:
                action = str(open_loop_next_steps[item.title])
            else:
                action = f"{item.title}の次の一手を決める"
            if action not in actions:
                actions.append(action)
        for memory in context_memories:
            title = _short_text(memory.content)
            if title in item_titles or title in known_task_titles:
                continue
            action = f"{title}を前提に今日の優先順位を決める"
            if action not in actions:
                actions.append(action)
        return actions

    @staticmethod
    def _confirmation_questions(items: list[DailyReviewItem]) -> list[str]:
        questions: list[str] = []
        for item in items:
            if item.source != "follow_up_candidate":
                continue
            question = f"「{item.title}」を続けますか？"
            if question not in questions:
                questions.append(question)
        return questions

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

    def _context_memories(self) -> list[Memory]:
        memories: list[Memory] = []
        for memory in self.store.list_memories(limit=10):
            if memory.kind not in DAILY_CONTEXT_MEMORY_KINDS:
                continue
            if memory.priority < DAILY_CONTEXT_MIN_PRIORITY:
                continue
            memories.append(memory)
            if len(memories) >= 3:
                break
        return memories


def _aware_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    if now.tzinfo is None:
        return now.replace(tzinfo=UTC)
    return now


def _short_text(text: str) -> str:
    compact = " ".join(str(text).split())
    if len(compact) <= DAILY_CONTEXT_TEXT_LIMIT:
        return compact
    return f"{compact[: DAILY_CONTEXT_TEXT_LIMIT - 3]}..."
