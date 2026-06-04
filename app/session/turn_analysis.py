from __future__ import annotations

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
