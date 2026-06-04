from datetime import UTC, datetime

from app.ai.backend_factory import describe_llm_backend
from app.daily import DailyReviewPlan
from app.io.voice import VoiceConfig
from app.memory.store import ApprovalRequest, Draft, Memory, MemoryStore, OpenLoop, parse_due_at
from app.session.manager import SessionManager


def print_banner(manager: SessionManager, voice_config: VoiceConfig) -> None:
    print("Orbit AI Terminal")
    print()
    print(f"AI name: {manager.assistant_display_name}")
    print(f"LLM backend: {describe_llm_backend(manager.profile)}")
    print("Start: AI greets or briefs you on launch")
    print("End: say something like 「ありがとう」 or 「ここまで」 during a conversation")
    print("Quit: /quit")
    print("Status: /status")
    print("Memory: /memory")
    print("Tasks: /tasks")
    print("Open loops: /loops")
    print("Approvals: /approvals")
    print("Drafts: /drafts")
    print("Daily review: /daily")
    print("Proactive check: /proactive")
    voice_input = "on (/voice or /v)" if voice_config.input_enabled else "off"
    print(f"Voice input: {voice_input}")
    print(f"Voice output: {'on' if voice_config.output_enabled else 'off'}")
    print()


def show_memory(store: MemoryStore) -> None:
    memories = store.list_memories()
    summaries = store.list_summaries()
    if not memories and not summaries:
        print("AI: No saved memory yet.")
        return
    if memories:
        print("AI: Saved memory:")
        for memory in memories:
            print(format_memory(memory))
    if summaries:
        print("AI: Recent summaries:")
        for summary in summaries:
            print(f"- {summary.summary}")
            for loop in summary.open_loops:
                print(f"  open_loop: {loop}")
            for follow_up in summary.follow_up_candidates:
                print(f"  follow_up: {follow_up}")


def show_memory_results(memories: list[Memory], empty_text: str = "AI: No matching memories.") -> None:
    if not memories:
        print(empty_text)
        return
    print("AI: Matching memories:")
    for memory in memories:
        print(format_memory(memory))


def show_memory_detail(memory: Memory | None) -> None:
    if memory is None:
        print("AI: Memory was not found.")
        return
    print("AI: Memory detail:")
    print(format_memory(memory))
    print(f"  status={memory.status} priority={memory.priority:.2f} confidence={memory.confidence:.2f}")
    if memory.source_session_id:
        print(f"  source_session_id={memory.source_session_id}")
    if memory.source_message_ids:
        print(f"  source_message_ids={','.join(str(value) for value in memory.source_message_ids)}")
    if memory.last_used_at:
        print(f"  last_used_at={memory.last_used_at} use_count={memory.use_count}")


def format_memory(memory: Memory) -> str:
    return f"- #{memory.id} [{memory.kind}] {memory.content}"


def show_open_loops(store: MemoryStore) -> None:
    loops = store.list_open_loops(statuses=("open", "snoozed"))
    if not loops:
        print("AI: No open loops.")
        return
    print("AI: Open loops:")
    for loop in loops:
        print(format_open_loop(loop))
        if loop.suggested_next_step:
            print(f"   next: {loop.suggested_next_step}")


def format_open_loop(loop: OpenLoop) -> str:
    return f"- #{loop.id} [{loop.status}] {loop.title}"


def show_tasks(store: MemoryStore) -> None:
    tasks = store.list_tasks(statuses=("open", "snoozed"))
    if not tasks:
        print("AI: No open tasks.")
        return
    print("AI: Tasks:")
    now = datetime.now(UTC)
    for task in tasks:
        due = ""
        if task.due_at:
            parsed_due_at = parse_due_at(task.due_at)
            if parsed_due_at is None:
                due_state = "unparsed"
            elif parsed_due_at <= now:
                due_state = "due"
            else:
                due_state = "waiting"
            due = f" due={task.due_at} ({due_state})"
        print(f"- #{task.id} [{task.status}] {task.title}{due}")


def show_approval_requests(store: MemoryStore) -> None:
    requests = store.list_approval_requests(status="pending")
    if not requests:
        print("AI: No pending approvals.")
        return
    print("AI: Pending approvals:")
    for request in requests:
        print(format_approval_request(request))


def format_approval_request(request: ApprovalRequest) -> str:
    summary = _approval_payload_summary(request)
    return f"- #{request.id} [{request.risk_level}] {request.action}: {summary}"


def show_drafts(store: MemoryStore) -> None:
    drafts = store.list_drafts(status="draft")
    if not drafts:
        print("AI: No drafts.")
        return
    print("AI: Drafts:")
    for draft in drafts:
        print(format_draft(draft))


def show_draft_detail(draft: Draft | None) -> None:
    if draft is None:
        print("AI: Draft was not found.")
        return
    print("AI: Draft detail:")
    print(format_draft(draft))
    print(draft.body)


def format_draft(draft: Draft) -> str:
    return f"- #{draft.id} [{draft.kind}/{draft.status}] {draft.title}"


def show_daily_review(plan: DailyReviewPlan) -> None:
    lines = plan.summary.splitlines()
    if not lines:
        return
    print(f"AI: {lines[0]}")
    for line in lines[1:]:
        print(line)


def _approval_payload_summary(request: ApprovalRequest) -> str:
    for key in ("title", "content", "text", "body"):
        value = request.payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return request.reason
