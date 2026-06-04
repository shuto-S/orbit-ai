from typing import Any

from app.actions.dispatcher import ActionRequest, ActionResult
from app.memory.store import MemoryStore
from app.text import sanitize_text


def create_text_draft(request: ActionRequest, store: MemoryStore) -> ActionResult:
    title = _string_payload(request.payload, "title", required=True)
    body = _string_payload(request.payload, "body", required=True)
    if title is None or body is None:
        return _invalid(request, "create_text_draft requires non-empty string title and body.")

    kind = _string_payload(request.payload, "kind") or "text"
    draft_id = store.add_draft(
        kind=kind,
        title=title,
        body=body,
        source_session_id=request.session_id,
        metadata={
            "request_id": request.request_id,
            "actor": request.actor,
            "source": request.source,
            "user_explicit": request.user_explicit,
        },
    )
    if draft_id is None:
        return ActionResult(
            action=request.action,
            ok=False,
            message="Draft was not created.",
            request_id=request.request_id,
            session_id=request.session_id,
            error_type="action_failed",
            data={"reason": "empty_title_or_body", "title": title},
        )
    return ActionResult(
        action=request.action,
        ok=True,
        message=f"Draft created: #{draft_id} {title}",
        request_id=request.request_id,
        session_id=request.session_id,
        data={"draft_id": draft_id, "title": title, "kind": kind},
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
