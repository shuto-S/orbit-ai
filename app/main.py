from app.config.loader import load_proactive_config, load_profile
from app.io.voice import VoiceConfig, VoiceIO
from app.latency import LatencyLogger
from app.memory.store import MemoryStore
from app.session.manager import SessionManager


def print_banner(manager: SessionManager, voice_config: VoiceConfig) -> None:
    print("Orbit AI Terminal")
    print()
    print(f"AI name: {manager.assistant_display_name}")
    print(f"Start: say something like 「{manager.assistant_display_name}、相談したい」")
    print("End: say something like 「ありがとう」 or 「ここまで」 during a conversation")
    print("Quit: /quit")
    print("Status: /status")
    print("Memory: /memory")
    print("Tasks: /tasks")
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
    for task in tasks:
        due = f" due={task.due_at}" if task.due_at else ""
        print(f"- #{task.id} [{task.status}] {task.title}{due}")


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


def main() -> None:
    profile = load_profile()
    proactive_config = load_proactive_config()
    latency = LatencyLogger.from_profile(profile)
    store = MemoryStore()
    manager = SessionManager(profile, proactive_config, store, latency=latency)
    voice = VoiceIO(VoiceConfig.from_profile(profile), latency=latency)
    print_banner(manager, voice.config)

    while True:
        try:
            user_text = voice.read_text()
        except (EOFError, KeyboardInterrupt):
            print()
            print("AI: 終了します。")
            voice.speak("終了します。")
            break

        if not user_text:
            continue
        if user_text == "/quit":
            print("AI: 終了します。")
            voice.speak("終了します。")
            break
        if user_text == "/status":
            print(f"AI: state={manager.state.value}, session_id={manager.session_id}")
            continue
        if user_text == "/memory":
            show_memory(store)
            continue
        if user_text == "/tasks":
            show_tasks(store)
            continue
        if user_text.startswith("/task "):
            handle_task_command(store, user_text)
            continue
        if user_text == "/reset":
            output = manager.reset()
            print(f"AI: {output.text}")
            if output.text:
                voice.speak(output.text)
            continue
        if user_text == "/proactive":
            decision = manager.check_proactive()
            if decision.allowed:
                output = manager.start_proactive_permission(decision.candidate.permission_text)
                print(f"AI: {output.text}")
                if output.text:
                    voice.speak(output.text)
            else:
                print(f"AI: proactive候補はありません。理由: {decision.reason}")
            continue

        latency.start_turn()
        latency.event("manager.handle_input.start")
        output = manager.handle_input(user_text)
        latency.event("manager.handle_input.end")
        if output.text:
            print(f"AI: {output.text}")
            voice.speak(output.text)


if __name__ == "__main__":
    main()
