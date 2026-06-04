from app.cli.display import (
    show_approval_requests,
    show_daily_review,
    show_draft_detail,
    show_drafts,
    show_memory_detail,
    show_memory_results,
)
from app.daily import DailyReviewPlan, DailyReviewService
from app.io.voice import VoiceIO
from app.memory.store import MemoryStore
from app.session.manager import SessionManager
from app.session.state import SessionState


def handle_daily_command(store: MemoryStore) -> DailyReviewPlan:
    plan = DailyReviewService(store).build_and_save()
    show_daily_review(plan)
    return plan


def handle_task_command(store: MemoryStore, user_text: str) -> bool:
    parts = user_text.split(maxsplit=3)
    if len(parts) < 3 or parts[0] != "/task":
        print("AI: Usage: /task done <id> or /task snooze <id> <when>")
        return True
    action = parts[1]
    try:
        task_id = int(parts[2])
    except ValueError:
        print("AI: task id must be a number.")
        return True
    if action == "done":
        if store.mark_task_done(task_id):
            print(f"AI: Task #{task_id} marked done.")
        else:
            print(f"AI: Task #{task_id} was not found.")
        return True
    if action == "snooze":
        if len(parts) < 4 or not parts[3].strip():
            print("AI: Usage: /task snooze <id> <when>")
            return True
        if store.snooze_task(task_id, parts[3].strip()):
            print(f"AI: Task #{task_id} snoozed until {parts[3].strip()}.")
        else:
            print(f"AI: Task #{task_id} was not found.")
        return True
    print("AI: Usage: /task done <id> or /task snooze <id> <when>")
    return True


def handle_approval_command(store: MemoryStore, user_text: str) -> bool:
    if user_text == "/approvals":
        show_approval_requests(store)
        return True

    parts = user_text.split(maxsplit=1)
    if len(parts) < 2 or parts[0] not in {"/approve", "/reject"}:
        print("AI: Usage: /approvals, /approve <id>, or /reject <id>")
        return True
    request_id = _parse_request_id(parts[1].strip())
    if request_id is None:
        print("AI: approval id must be a number.")
        return True

    if parts[0] == "/approve":
        request = store.approve_request(request_id)
        if request is None:
            print(f"AI: Approval #{request_id} was not found.")
        else:
            print(f"AI: Approval #{request_id} approved.")
        return True

    request = store.reject_request(request_id)
    if request is None:
        print(f"AI: Approval #{request_id} was not found.")
    else:
        print(f"AI: Approval #{request_id} rejected.")
    return True


def handle_draft_command(store: MemoryStore, user_text: str) -> bool:
    if user_text == "/drafts":
        show_drafts(store)
        return True

    parts = user_text.split(maxsplit=2)
    if len(parts) < 3 or parts[0] != "/draft":
        print("AI: Usage: /drafts, /draft show <id>, or /draft archive <id>")
        return True
    action = parts[1]
    draft_id = _parse_request_id(parts[2].strip())
    if draft_id is None:
        print("AI: draft id must be a number.")
        return True
    if action == "show":
        show_draft_detail(store.get_draft(draft_id))
        return True
    if action == "archive":
        draft = store.archive_draft(draft_id)
        if draft is None:
            print(f"AI: Draft #{draft_id} was not found.")
        else:
            print(f"AI: Draft #{draft_id} archived.")
        return True
    print("AI: Usage: /drafts, /draft show <id>, or /draft archive <id>")
    return True


def handle_loop_command(store: MemoryStore, user_text: str) -> bool:
    parts = user_text.split(maxsplit=2)
    if len(parts) < 3 or parts[0] != "/loop":
        print("AI: Usage: /loop done <id> or /loop archive <id>")
        return True
    action = parts[1]
    try:
        loop_id = int(parts[2])
    except ValueError:
        print("AI: loop id must be a number.")
        return True
    if action == "done":
        if store.resolve_open_loop(loop_id):
            print(f"AI: Open loop #{loop_id} marked resolved.")
        else:
            print(f"AI: Open loop #{loop_id} was not found.")
        return True
    if action == "archive":
        if store.archive_open_loop(loop_id):
            print(f"AI: Open loop #{loop_id} archived.")
        else:
            print(f"AI: Open loop #{loop_id} was not found.")
        return True
    print("AI: Usage: /loop done <id> or /loop archive <id>")
    return True


def handle_memory_command(store: MemoryStore, user_text: str) -> bool:
    if user_text.startswith("/remember "):
        content = user_text.removeprefix("/remember ").strip()
        if not content:
            print("AI: Usage: /remember <text>")
            return True
        memory_id = store.add_memory("manual", content, priority=0.9, confidence=1.0)
        if memory_id is None:
            print("AI: この内容は記憶に保存しませんでした。")
        else:
            print(f"AI: Memory #{memory_id} saved.")
        return True

    if user_text.startswith("/forget "):
        memory_id = _parse_memory_id(user_text.removeprefix("/forget ").strip())
        if memory_id is None:
            print("AI: Usage: /forget <id>")
            return True
        if store.forget_memory(memory_id):
            print(f"AI: Memory #{memory_id} forgotten.")
        else:
            print(f"AI: Memory #{memory_id} was not found.")
        return True

    parts = user_text.split(maxsplit=2)
    if len(parts) < 2 or parts[0] != "/memory":
        print(
            "AI: Usage: /memory search <query>, /memory show <id>, "
            "/memory archive <id>, /remember <text>, /forget <id>"
        )
        return True

    action = parts[1]
    value = parts[2].strip() if len(parts) >= 3 else ""
    if action == "search":
        if not value:
            print("AI: Usage: /memory search <query>")
            return True
        show_memory_results(store.search_memories(value, limit=10))
        return True
    if action == "show":
        memory_id = _parse_memory_id(value)
        if memory_id is None:
            print("AI: Usage: /memory show <id>")
            return True
        show_memory_detail(store.get_memory(memory_id))
        return True
    if action == "archive":
        memory_id = _parse_memory_id(value)
        if memory_id is None:
            print("AI: Usage: /memory archive <id>")
            return True
        if store.archive_memory(memory_id):
            print(f"AI: Memory #{memory_id} archived.")
        else:
            print(f"AI: Memory #{memory_id} was not found.")
        return True
    print("AI: Usage: /memory search <query>, /memory show <id>, /memory archive <id>")
    return True


def handle_proactive_command(manager: SessionManager, voice: VoiceIO) -> bool:
    decision = manager.check_proactive(trigger="manual")
    if not decision.allowed:
        print(f"AI: proactive候補はありません。理由: {decision.reason}")
        return False
    if manager.state != SessionState.IDLE:
        print(f"AI: proactive候補はありますが、現在の状態では開始できません。state={manager.state.value}")
        return False
    output = manager.start_proactive_permission(decision.candidate.permission_text, decision.candidate)
    if output.text:
        print(f"AI: {output.text}")
        voice.speak(output.text)
    return True


def _parse_memory_id(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def _parse_request_id(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None
