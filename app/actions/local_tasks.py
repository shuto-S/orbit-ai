from typing import Any

from app.actions.dispatcher import ActionRequest, ActionResult
from app.memory.store import MemoryStore
from app.text import sanitize_text


def create_task(request: ActionRequest, store: MemoryStore) -> ActionResult:
    title = _string_payload(request.payload, "title", required=True)
    if title is None:
        return _invalid(request, "create_task requires a non-empty string title.")

    source = _string_payload(request.payload, "source") or "manual"
    description = _string_payload(request.payload, "description")
    due_at = _string_payload(request.payload, "due_at")
    source_session_id = _string_payload(request.payload, "source_session_id") or request.session_id
    priority = _float_payload(request.payload, "priority", default=0.5)
    if priority is None:
        return _invalid(request, "create_task priority must be a number.")

    task_id = store.add_task(
        title=title,
        source=source,
        source_session_id=source_session_id,
        description=description,
        priority=priority,
        due_at=due_at,
    )
    if task_id is None:
        return ActionResult(
            action=request.action,
            ok=False,
            message="Task was not created.",
            request_id=request.request_id,
            session_id=request.session_id,
            error_type="action_failed",
            data={"reason": "empty_or_duplicate_title", "title": title},
        )
    return ActionResult(
        action=request.action,
        ok=True,
        message=f"Task #{task_id} created.",
        request_id=request.request_id,
        session_id=request.session_id,
        data={"task_id": task_id, "title": title},
    )


def mark_task_done(request: ActionRequest, store: MemoryStore) -> ActionResult:
    task_id = _int_payload(request.payload, "task_id")
    if task_id is None:
        return _invalid(request, "mark_task_done requires an integer task_id.")

    if not store.mark_task_done(task_id):
        return ActionResult(
            action=request.action,
            ok=False,
            message=f"Task #{task_id} was not found.",
            request_id=request.request_id,
            session_id=request.session_id,
            error_type="not_found",
            data={"task_id": task_id},
        )
    return ActionResult(
        action=request.action,
        ok=True,
        message=f"Task #{task_id} marked done.",
        request_id=request.request_id,
        session_id=request.session_id,
        data={"task_id": task_id, "status": "done"},
    )


def snooze_task(request: ActionRequest, store: MemoryStore) -> ActionResult:
    task_id = _int_payload(request.payload, "task_id")
    due_at = _string_payload(request.payload, "due_at", required=True)
    if task_id is None or due_at is None:
        return _invalid(request, "snooze_task requires an integer task_id and non-empty string due_at.")

    if not store.snooze_task(task_id, due_at):
        return ActionResult(
            action=request.action,
            ok=False,
            message=f"Task #{task_id} was not found or due_at was invalid.",
            request_id=request.request_id,
            session_id=request.session_id,
            error_type="not_found",
            data={"task_id": task_id, "due_at": due_at},
        )
    return ActionResult(
        action=request.action,
        ok=True,
        message=f"Task #{task_id} snoozed until {due_at}.",
        request_id=request.request_id,
        session_id=request.session_id,
        data={"task_id": task_id, "status": "snoozed", "due_at": due_at},
    )


def _invalid(request: ActionRequest, message: str) -> ActionResult:
    return ActionResult(
        action=request.action,
        ok=False,
        message=message,
        request_id=request.request_id,
        session_id=request.session_id,
        error_type="invalid_payload",
    )


def _string_payload(payload: dict[str, Any], key: str, required: bool = False) -> str | None:
    value = payload.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str):
        return None
    sanitized = sanitize_text(value).strip()
    if not sanitized:
        return None
    return sanitized


def _int_payload(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _float_payload(payload: dict[str, Any], key: str, default: float) -> float | None:
    value = payload.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)
