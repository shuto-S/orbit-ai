from __future__ import annotations

from typing import Any

from app.ai.turn_analysis_agent import TurnAnalysis, TurnAnalysisAgent
from app.memory.store import MemoryStore
from app.text import sanitize_text


def run_turn_analysis(
    agent: TurnAnalysisAgent,
    store: MemoryStore,
    session_id: str,
    user_text: str,
    assistant_text: str,
) -> TurnAnalysis:
    try:
        analysis = agent.analyze(user_text=user_text, assistant_text=assistant_text)
    except Exception as exc:
        analysis = TurnAnalysis.empty(status="analysis_error", failure_reason=type(exc).__name__)

    store.add_decision_log(
        kind="turn_analysis",
        session_id=session_id,
        candidate_text=_candidate_text(analysis),
        decision="recorded" if analysis.has_candidates() else "empty",
        reason=analysis.status,
        score=analysis.max_confidence(),
        metadata=analysis.to_metadata(),
    )
    _queue_approval_requests(store, session_id, analysis)
    return analysis


def _candidate_text(analysis: TurnAnalysis) -> str | None:
    if analysis.task_candidates:
        return sanitize_text(analysis.task_candidates[0].title)
    if analysis.open_loop_candidates:
        return sanitize_text(analysis.open_loop_candidates[0].title)
    if analysis.follow_up_candidates:
        return sanitize_text(analysis.follow_up_candidates[0].text)
    if analysis.memory_candidates:
        return sanitize_text(analysis.memory_candidates[0].content)
    return None


def _queue_approval_requests(store: MemoryStore, session_id: str, analysis: TurnAnalysis) -> None:
    for candidate in analysis.task_candidates:
        if not candidate.needs_confirmation:
            continue
        store.add_approval_request(
            action="create_task",
            payload={
                "title": candidate.title,
                "due_at": candidate.due_text,
                "priority": candidate.confidence,
                "source": "turn_analysis",
                "source_session_id": session_id,
            },
            reason=f"turn analysis task candidate: {candidate.source_text}",
            source_session_id=session_id,
            metadata={"candidate_type": "task", "confidence": candidate.confidence},
        )
    for candidate in analysis.follow_up_candidates:
        store.add_approval_request(
            action="create_task",
            payload={
                "title": candidate.text,
                "due_at": candidate.due_text,
                "priority": candidate.confidence,
                "source": "turn_analysis_follow_up",
                "source_session_id": session_id,
            },
            reason=f"turn analysis follow-up candidate: {candidate.reason or candidate.text}",
            source_session_id=session_id,
            metadata={"candidate_type": "follow_up", "confidence": candidate.confidence},
        )
    for candidate in analysis.memory_candidates:
        if not candidate.needs_confirmation:
            continue
        store.add_approval_request(
            action="save_memory",
            payload={
                "kind": candidate.kind,
                "content": candidate.content,
                "confidence": candidate.confidence,
                "source_session_id": session_id,
            },
            reason=f"turn analysis memory candidate: {candidate.source_text}",
            source_session_id=session_id,
            metadata={"candidate_type": "memory", "confidence": candidate.confidence},
        )
    for action_request in analysis.permission_required_actions:
        action = _action_name(action_request)
        payload = _action_payload(action_request)
        store.add_approval_request(
            action=action,
            payload=payload,
            reason=str(action_request.get("reason") or "turn analysis permission required action"),
            risk_level=str(action_request.get("risk_level") or "normal"),
            source_session_id=session_id,
            metadata={"candidate_type": "permission_required_action"},
        )


def _action_name(action_request: dict[str, Any]) -> str:
    action = action_request.get("action")
    return sanitize_text(str(action)).strip() if action else "unknown"


def _action_payload(action_request: dict[str, Any]) -> dict[str, Any]:
    reserved_keys = {"action", "reason", "risk_level"}
    payload = action_request.get("payload")
    if isinstance(payload, dict):
        return payload
    return {key: value for key, value in action_request.items() if key not in reserved_keys}
