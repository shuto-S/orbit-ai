from typing import Any

from app.actions.dispatcher import ActionRequest, ActionResult
from app.autonomous.reminders import create_reminder_job, parse_reminder_request
from app.memory.store import MemoryStore
from app.text import sanitize_text


def create_reminder(request: ActionRequest, store: MemoryStore) -> ActionResult:
    text = _string_payload(request.payload, "text", required=True)
    when = _string_payload(request.payload, "when", required=True)
    timezone = _string_payload(request.payload, "timezone") or "Asia/Tokyo"
    if text is None or when is None:
        return _invalid(request, "create_reminder requires non-empty string text and when.")

    reminder = parse_reminder_request(f"{when} {text}", default_timezone=timezone)
    if reminder is None or not reminder.text:
        return _invalid(request, "create_reminder could not parse when.")

    job_id = create_reminder_job(
        store,
        reminder,
        source=_string_payload(request.payload, "source") or request.source or "action",
        source_session_id=_string_payload(request.payload, "source_session_id") or request.session_id,
    )
    if job_id is None:
        return ActionResult(
            action=request.action,
            ok=False,
            message="Reminder was not created.",
            request_id=request.request_id,
            session_id=request.session_id,
            error_type="action_failed",
            data={"text": text, "when": when},
        )
    return ActionResult(
        action=request.action,
        ok=True,
        message=f"Reminder job #{job_id} created.",
        request_id=request.request_id,
        session_id=request.session_id,
        data={"job_id": job_id, "text": reminder.text, "next_run_at": reminder.due_at.isoformat()},
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
