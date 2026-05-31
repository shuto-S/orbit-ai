from app.memory.extractor import MemoryExtractor
from app.memory.store import MemoryStore
from app.memory.summarizer import SessionSummarizer


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
    assistant_text = "わかりました。また呼んでください。"
    store.add_message(session_id, "assistant", assistant_text)
    return assistant_text
