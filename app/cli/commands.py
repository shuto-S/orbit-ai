from app.cli.display import show_daily_review
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


def handle_proactive_command(manager: SessionManager, voice: VoiceIO) -> bool:
    decision = manager.check_proactive(trigger="manual")
    if not decision.allowed:
        print(f"AI: proactive候補はありません。理由: {decision.reason}")
        return False
    if manager.state != SessionState.IDLE:
        print(f"AI: proactive候補はありますが、現在の状態では開始できません。state={manager.state.value}")
        return False
    output = manager.start_proactive_permission(decision.candidate.permission_text)
    if output.text:
        print(f"AI: {output.text}")
        voice.speak(output.text)
    return True
