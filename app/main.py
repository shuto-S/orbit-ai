import select
import sys
from collections.abc import Callable

from app.config.loader import load_autonomy_config, load_proactive_config, load_profile
from app.io.voice import VoiceConfig, VoiceIO
from app.latency import LatencyLogger
from app.memory.store import MemoryStore
from app.session.manager import SessionManager
from app.session.state import SessionState
from app.text import sanitize_text

DEFAULT_PROACTIVE_CHECK_INTERVAL_SECONDS = 30


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


def proactive_check_interval_seconds(proactive_config: dict[str, object]) -> int:
    try:
        interval = int(proactive_config.get("check_interval_seconds", DEFAULT_PROACTIVE_CHECK_INTERVAL_SECONDS))
    except (TypeError, ValueError):
        return DEFAULT_PROACTIVE_CHECK_INTERVAL_SECONDS
    return max(1, interval)


def maybe_start_proactive_permission(manager: SessionManager, voice: VoiceIO, leading_newline: bool = False) -> bool:
    if manager.state != SessionState.IDLE:
        return False

    decision = manager.check_proactive()
    if not decision.allowed:
        return False

    output = manager.start_proactive_permission(decision.candidate.permission_text)
    if output.text:
        if leading_newline:
            print()
        print(f"AI: {output.text}")
        voice.speak(output.text)
    return True


def read_text_with_idle_ticks(
    voice: VoiceIO,
    check_interval_seconds: int,
    on_idle_tick: Callable[[], bool],
) -> str:
    if voice.config.input_enabled:
        on_idle_tick()
        user_text = voice.read_text()
        on_idle_tick()
        return user_text

    prompt_shown = False
    while True:
        if not prompt_shown:
            sys.stdout.write("User: ")
            sys.stdout.flush()
            prompt_shown = True
        readable, _, _ = select.select([sys.stdin], [], [], check_interval_seconds)
        if not readable:
            if on_idle_tick():
                sys.stdout.write("User: ")
                sys.stdout.flush()
            continue

        line = sys.stdin.readline()
        if line == "":
            raise EOFError
        return sanitize_text(line).strip()


def main() -> None:
    profile = load_profile()
    proactive_config = load_proactive_config()
    autonomy_config = load_autonomy_config(profile)
    check_interval_seconds = proactive_check_interval_seconds(proactive_config)
    latency = LatencyLogger.from_profile(profile)
    store = MemoryStore()
    manager = SessionManager(profile, proactive_config, store, autonomy_config=autonomy_config, latency=latency)
    voice = VoiceIO(VoiceConfig.from_profile(profile), latency=latency)
    print_banner(manager, voice.config)

    while True:
        latency.start_turn(session_id=manager.session_id)
        try:
            user_text = read_text_with_idle_ticks(
                voice,
                check_interval_seconds,
                lambda: maybe_start_proactive_permission(manager, voice, leading_newline=True),
            )
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
                if not maybe_start_proactive_permission(manager, voice):
                    print(f"AI: proactive候補はありますが、現在の状態では開始できません。state={manager.state.value}")
            else:
                print(f"AI: proactive候補はありません。理由: {decision.reason}")
            continue

        latency.event("manager.handle_input.start")
        output = manager.handle_input(user_text)
        latency.bind_session(output.session_id)
        latency.event("manager.handle_input.end")
        if output.text:
            print(f"AI: {output.text}")
            voice.speak(output.text)


if __name__ == "__main__":
    main()
