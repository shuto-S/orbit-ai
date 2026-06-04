from __future__ import annotations

from dataclasses import dataclass

from app.memory.models import AutonomousNotification, Memory, OpenLoop, SessionSummary, Task
from app.memory.store import MemoryStore
from app.text import sanitize_text

MAX_GROUNDED_ITEMS = 5


@dataclass(frozen=True)
class SourceReference:
    kind: str
    id: str
    title: str
    detail: str | None = None


@dataclass(frozen=True)
class GroundedResponse:
    text: str
    sources: list[SourceReference]


def maybe_grounded_response(
    user_text: str,
    store: MemoryStore,
    memories: list[Memory],
    last_sources: list[SourceReference],
) -> GroundedResponse | None:
    text = sanitize_text(user_text).strip()
    if not text:
        return None
    normalized = _normalize(text)
    if _is_source_question(normalized):
        return _source_response(last_sources)
    if _is_schedule_question(normalized):
        return _schedule_response(store)
    if _is_pr_question(normalized):
        return _external_info_response(
            store=store,
            memories=memories,
            keywords=("pr", "pull request", "プルリクエスト", "プルリク", "github"),
            found_prefix="GitHubには直接アクセスしていません。ローカル記録上は、",
            not_found="GitHub PR にはアクセスしていないため、どのPRかは確認できません。",
        )
    if _is_email_question(normalized):
        return _external_info_response(
            store=store,
            memories=memories,
            keywords=("mail", "email", "メール", "未読"),
            found_prefix="メールには直接アクセスしていません。ローカル記録上は、",
            not_found="メールにはアクセスしていないため、未読メールや本文は確認できません。記録済みタスクなら確認できます。",
        )
    return None


def format_sources(sources: list[SourceReference]) -> str:
    if not sources:
        return "確認済みソースはありません。"
    return "\n".join(f"ソース: {_format_source(source)}" for source in sources[:MAX_GROUNDED_ITEMS])


def _source_response(last_sources: list[SourceReference]) -> GroundedResponse:
    if not last_sources:
        return GroundedResponse("直前の回答には確認済みソースがありません。", [])
    return GroundedResponse(format_sources(last_sources), last_sources)


def _schedule_response(store: MemoryStore) -> GroundedResponse:
    tasks = store.list_tasks(statuses=("open", "snoozed"), limit=MAX_GROUNDED_ITEMS)
    if not tasks:
        return GroundedResponse(
            "現在、予定表にアクセスできないため今日の予定は確認できません。記録済みタスクなら確認できます。",
            [],
        )
    sources = [_task_source(task) for task in tasks]
    task_lines = "\n".join(f"- {task.title}{_task_due_suffix(task)}" for task in tasks)
    return GroundedResponse(
        f"予定表ではなく記録済みタスクとして、以下があります。\n{task_lines}\n{format_sources(sources)}",
        sources,
    )


def _external_info_response(
    store: MemoryStore,
    memories: list[Memory],
    keywords: tuple[str, ...],
    found_prefix: str,
    not_found: str,
) -> GroundedResponse:
    sources = _local_sources_matching(store, memories, keywords)
    if not sources:
        return GroundedResponse(not_found, [])
    titles = " / ".join(f"{source.title}" for source in sources[:MAX_GROUNDED_ITEMS])
    return GroundedResponse(f"{found_prefix}{titles} です。\n{format_sources(sources)}", sources)


def _local_sources_matching(
    store: MemoryStore,
    memories: list[Memory],
    keywords: tuple[str, ...],
) -> list[SourceReference]:
    sources: list[SourceReference] = []
    seen: set[tuple[str, str]] = set()
    for memory in memories:
        _append_if_matches(sources, seen, _memory_source(memory), keywords)
    for memory in store.list_memories(limit=50):
        _append_if_matches(sources, seen, _memory_source(memory), keywords)
    for task in store.list_tasks(statuses=("open", "snoozed"), limit=50):
        _append_if_matches(sources, seen, _task_source(task), keywords)
    for loop in store.list_open_loops(statuses=("open", "snoozed"), limit=50):
        _append_if_matches(sources, seen, _open_loop_source(loop), keywords)
    for summary in store.list_summaries(limit=20):
        _append_if_matches(sources, seen, _summary_source(summary), keywords)
    for notification in store.list_autonomous_notifications(status=None, limit=20):
        _append_if_matches(sources, seen, _notification_source(notification), keywords)
    return sources[:MAX_GROUNDED_ITEMS]


def _append_if_matches(
    sources: list[SourceReference],
    seen: set[tuple[str, str]],
    source: SourceReference,
    keywords: tuple[str, ...],
) -> None:
    key = (source.kind, source.id)
    if key in seen:
        return
    haystack = _normalize(f"{source.title} {source.detail or ''}")
    if any(keyword in haystack for keyword in keywords):
        sources.append(source)
        seen.add(key)


def _task_source(task: Task) -> SourceReference:
    detail_parts = [f"status={task.status}"]
    if task.source:
        detail_parts.append(f"source={task.source}")
    if task.due_at:
        detail_parts.append(f"due_at={task.due_at}")
    return SourceReference("task", str(task.id), task.title, ", ".join(detail_parts))


def _open_loop_source(loop: OpenLoop) -> SourceReference:
    detail = f"status={loop.status}"
    if loop.source_session_id:
        detail += f", session={loop.source_session_id}"
    return SourceReference("open_loop", str(loop.id), loop.title, detail)


def _memory_source(memory: Memory) -> SourceReference:
    detail = f"kind={memory.kind}, confidence={memory.confidence:.2f}"
    if memory.source_session_id:
        detail += f", session={memory.source_session_id}"
    return SourceReference("memory", str(memory.id), memory.content, detail)


def _summary_source(summary: SessionSummary) -> SourceReference:
    parts = [summary.summary, *summary.open_loops, *summary.follow_up_candidates]
    title = " / ".join(part for part in parts if part) or summary.session_id
    return SourceReference("summary", summary.session_id, title, f"session={summary.session_id}")


def _notification_source(notification: AutonomousNotification) -> SourceReference:
    detail = f"status={notification.status}"
    if notification.job_id is not None:
        detail += f", job_id={notification.job_id}"
    return SourceReference("autonomous_notification", str(notification.id), notification.title, detail)


def _format_source(source: SourceReference) -> str:
    detail = f" ({source.detail})" if source.detail else ""
    return f"{source.kind} #{source.id} 「{source.title}」{detail}"


def _task_due_suffix(task: Task) -> str:
    return f"（due_at: {task.due_at}）" if task.due_at else ""


def _is_schedule_question(normalized: str) -> bool:
    if not any(word in normalized for word in ("予定", "スケジュール", "calendar", "schedule")):
        return False
    if any(word in normalized for word in ("整理", "作成", "登録", "保存", "追加", "調整")):
        return False
    return any(word in normalized for word in ("今日", "明日", "本日", "今週", "ある", "なに", "何", "教えて", "確認"))


def _is_source_question(normalized: str) -> bool:
    return any(word in normalized for word in ("ソース", "source", "根拠", "どこから", "出典"))


def _is_pr_question(normalized: str) -> bool:
    if not any(word in normalized for word in ("pr", "pull request", "プルリク", "プルリクエスト")):
        return False
    return any(word in normalized for word in ("どの", "どれ", "どこ", "何", "確認", "教えて"))


def _is_email_question(normalized: str) -> bool:
    if not any(word in normalized for word in ("メール", "mail", "email", "未読")):
        return False
    return any(word in normalized for word in ("未読", "ある", "確認", "要約", "どれ", "何", "教えて"))


def _normalize(text: str) -> str:
    return text.strip().lower()
