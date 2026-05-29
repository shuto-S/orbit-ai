from datetime import UTC, datetime

from app.daily import DailyReviewPlan
from app.io.voice import VoiceConfig
from app.memory.store import MemoryStore, parse_due_at
from app.session.manager import SessionManager


def print_banner(manager: SessionManager, voice_config: VoiceConfig) -> None:
    print("Orbit AI Terminal")
    print()
    print(f"AI name: {manager.assistant_display_name}")
    print("Start: AI greets you on launch")
    print("End: say something like 「ありがとう」 or 「ここまで」 during a conversation")
    print("Quit: /quit")
    print("Status: /status")
    print("Memory: /memory")
    print("Tasks: /tasks")
    print("Daily review: /daily")
    print("Proactive check: /proactive")
    print(f"Voice input: {'on' if voice_config.input_enabled else 'off'}")
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
            print(f"- #{memory.id} [{memory.kind}] {memory.content}")
    if summaries:
        print("AI: Recent summaries:")
        for summary in summaries:
            print(f"- {summary.summary}")
            for loop in summary.open_loops:
                print(f"  open_loop: {loop}")
            for follow_up in summary.follow_up_candidates:
                print(f"  follow_up: {follow_up}")


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


def show_daily_review(plan: DailyReviewPlan) -> None:
    if not plan.items:
        print("AI: 今日の確認候補はありません。")
    else:
        print("AI: 今日の確認候補です。")
        for item in plan.items:
            prefix = f"[{item.source}]"
            if item.id is not None:
                prefix = f"[{item.source} #{item.id}]"
            print(f"- {prefix} {item.title} ({item.reason})")

    if plan.open_tasks:
        print("AI: Open tasks:")
        for task in plan.open_tasks:
            due = f" due={task.due_at}" if task.due_at else ""
            print(f"- #{task.id} {task.title}{due}")
    if plan.snoozed_tasks:
        print("AI: Snoozed tasks:")
        for task in plan.snoozed_tasks:
            due = f" due={task.due_at}" if task.due_at else ""
            print(f"- #{task.id} {task.title}{due}")
    if plan.recent_summaries:
        print("AI: Recent summaries:")
        for summary in plan.recent_summaries:
            print(f"- {summary.summary}")
    if plan.open_loops:
        print("AI: Open loops:")
        for loop in plan.open_loops:
            print(f"- {loop}")
    if plan.follow_up_candidates:
        print("AI: Follow-up candidates:")
        for follow_up in plan.follow_up_candidates:
            print(f"- {follow_up}")
