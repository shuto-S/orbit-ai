from app.memory.extractor import MemoryExtractor
from app.memory.store import MemoryStore
from app.memory.summarizer import SessionSummarizer
from app.session.resume_point import build_session_resume_point


def close_session(
    store: MemoryStore,
    session_id: str,
    summarizer: SessionSummarizer,
    extractor: MemoryExtractor,
) -> str:
    messages = store.get_session_messages(session_id)
    summary = summarizer.summarize(messages)
    store.add_summary(
        session_id=session_id,
        summary=str(summary["summary"]),
        open_loops=list(summary["open_loops"]),
        decisions=list(summary["decisions"]),
        follow_up_candidates=list(summary["follow_up_candidates"]),
    )
    resume_point = build_session_resume_point(messages, summary)
    if resume_point is not None:
        store.add_open_loop(
            title=resume_point.title,
            summary=str(summary["summary"]),
            source_session_id=session_id,
            suggested_next_step=resume_point.suggested_next_action,
            importance=0.8,
            confidence=0.75,
            metadata={"source": "session_close", "kind": "next_resume_point", "reason": resume_point.reason},
        )
        store.add_decision_log(
            kind="session_resume_point",
            session_id=session_id,
            candidate_text=resume_point.title,
            decision="recorded",
            reason=resume_point.reason,
            score=0.75,
            metadata={"suggested_next_action": resume_point.suggested_next_action},
        )
    store.add_tasks_from_summary(
        session_id=session_id,
        open_loops=list(summary["open_loops"]),
        follow_up_candidates=list(summary["follow_up_candidates"]),
    )
    for memory in extractor.extract(messages):
        store.add_memory(
            memory.kind,
            memory.content,
            memory.priority,
            memory.confidence,
            source_session_id=session_id,
            source_message_ids=memory.source_message_ids,
            sensitivity=memory.sensitivity,
            expires_at=memory.expires_at,
        )
    if resume_point is None:
        assistant_text = "わかりました。また呼んでください。"
    else:
        assistant_text = f"わかりました。また呼んでください。次回は「{resume_point.title}」から再開できます。"
    store.add_message(session_id, "assistant", assistant_text)
    return assistant_text
