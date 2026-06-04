import select
import sys
from collections.abc import Callable

from app.autonomous.runtime import AutonomousRuntime
from app.cli.commands import (
    handle_approval_command,
    handle_daily_command,
    handle_draft_command,
    handle_jobs_command,
    handle_loop_command,
    handle_memory_command,
    handle_notifications_command,
    handle_proactive_command,
    handle_remind_command,
    handle_task_command,
)
from app.cli.display import show_memory, show_open_loops, show_tasks
from app.cli.progress import AgentProgressDisplay
from app.io.voice import VoiceIO
from app.latency import LatencyLogger
from app.memory.store import MemoryStore
from app.session.manager import SessionManager
from app.session.state import SessionState
from app.text import sanitize_text

DEFAULT_PROACTIVE_CHECK_INTERVAL_SECONDS = 30
THINKING_ACKNOWLEDGEMENT = "確認しますね。"
VOICE_INPUT_COMMANDS = {"/v", "/voice"}


def proactive_check_interval_seconds(proactive_config: dict[str, object]) -> int:
    try:
        interval = int(proactive_config.get("check_interval_seconds", DEFAULT_PROACTIVE_CHECK_INTERVAL_SECONDS))
    except (TypeError, ValueError):
        return DEFAULT_PROACTIVE_CHECK_INTERVAL_SECONDS
    return max(1, interval)


def maybe_start_proactive_permission(manager: SessionManager, voice: VoiceIO, leading_newline: bool = False) -> bool:
    if manager.state != SessionState.IDLE:
        return False

    decision = manager.check_proactive(trigger="idle")
    if not decision.allowed:
        return False

    output = manager.start_proactive_permission(decision.candidate.permission_text, decision.candidate)
    if output.text:
        if leading_newline:
            print()
        print(f"AI: {output.text}")
        voice.speak_async(output.text)
    return True


def announce_shutdown(voice: VoiceIO, leading_newline: bool = True) -> None:
    if leading_newline:
        print()
    print("AI: 終了します。")
    try:
        voice.speak("終了します。")
    except KeyboardInterrupt:
        voice.stop_speaking()


def read_text_with_idle_ticks(
    voice: VoiceIO,
    check_interval_seconds: int,
    on_idle_tick: Callable[[], bool],
) -> str:
    if _is_interactive_stdin():
        return _read_interactive_text(voice, on_idle_tick)

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
        user_text = sanitize_text(line).strip()
        if user_text in VOICE_INPUT_COMMANDS:
            if not voice.config.input_enabled:
                print("AI: 音声入力は無効です。")
                prompt_shown = False
                continue
            on_idle_tick()
            voice_text = voice.read_voice_text()
            on_idle_tick()
            return voice_text
        return user_text


def _is_interactive_stdin() -> bool:
    isatty = getattr(sys.stdin, "isatty", lambda: False)
    return bool(isatty())


def _read_interactive_text(voice: VoiceIO, on_idle_tick: Callable[[], bool]) -> str:
    while True:
        user_text = sanitize_text(input("User: ")).strip()
        if user_text in VOICE_INPUT_COMMANDS:
            if not voice.config.input_enabled:
                print("AI: 音声入力は無効です。")
                continue
            on_idle_tick()
            voice_text = voice.read_voice_text()
            on_idle_tick()
            return voice_text
        return user_text


def run_terminal_loop(
    manager: SessionManager,
    voice: VoiceIO,
    store: MemoryStore,
    latency: LatencyLogger,
    check_interval_seconds: int,
    autonomous_runtime: AutonomousRuntime | None = None,
) -> None:
    try:
        if autonomous_runtime is not None:
            autonomous_runtime.start()
        startup_output = manager.start_conversation()
        if startup_output.text:
            print(f"AI: {startup_output.text}")
            voice.speak_async(startup_output.text)
        if autonomous_runtime is not None:
            autonomous_runtime.run_once()

        while True:
            latency.start_turn(session_id=manager.session_id)
            try:
                user_text = read_text_with_idle_ticks(
                    voice,
                    check_interval_seconds,
                    lambda: maybe_start_proactive_permission(manager, voice, leading_newline=True),
                )
            except EOFError:
                announce_shutdown(voice)
                break

            if not user_text:
                continue
            voice.stop_speaking()
            if user_text == "/quit":
                announce_shutdown(voice, leading_newline=False)
                break
            if user_text == "/status":
                print(f"AI: state={manager.state.value}, session_id={manager.session_id}")
                continue
            if user_text == "/memory":
                show_memory(store)
                continue
            if (
                user_text.startswith("/memory ")
                or user_text.startswith("/remember ")
                or user_text.startswith("/forget ")
            ):
                handle_memory_command(store, user_text)
                continue
            if user_text == "/tasks":
                show_tasks(store)
                continue
            if user_text.startswith("/remind"):
                handle_remind_command(
                    store,
                    user_text,
                    default_timezone=str(manager.autonomous_config.get("default_timezone") or "Asia/Tokyo"),
                )
                continue
            if user_text == "/jobs" or user_text.startswith("/job "):
                handle_jobs_command(store, user_text)
                continue
            if user_text == "/notifications":
                handle_notifications_command(store)
                continue
            if user_text == "/approvals" or user_text.startswith("/approve ") or user_text.startswith("/reject "):
                handle_approval_command(store, user_text)
                continue
            if user_text == "/drafts" or user_text.startswith("/draft "):
                handle_draft_command(store, user_text)
                continue
            if user_text == "/loops":
                show_open_loops(store)
                continue
            if user_text in ("/daily", "/review"):
                handle_daily_command(store)
                continue
            if user_text.startswith("/task "):
                handle_task_command(store, user_text)
                continue
            if user_text.startswith("/loop "):
                handle_loop_command(store, user_text)
                continue
            if user_text == "/reset":
                output = manager.reset()
                print(f"AI: {output.text}")
                if output.text:
                    voice.speak_async(output.text)
                continue
            if user_text == "/proactive":
                handle_proactive_command(manager, voice)
                continue

            print(f"AI: {THINKING_ACKNOWLEDGEMENT}")
            voice.speak_async(THINKING_ACKNOWLEDGEMENT)
            latency.event("manager.handle_input.start")
            with AgentProgressDisplay() as progress:
                output = manager.handle_input(user_text, progress_callback=progress.show)
            latency.bind_session(output.session_id)
            latency.event("manager.handle_input.end")
            if output.text:
                print(f"AI: {output.text}")
                voice.speak_async(output.text)
            if autonomous_runtime is not None:
                autonomous_runtime.deliver_pending()
    except KeyboardInterrupt:
        announce_shutdown(voice)
    finally:
        if autonomous_runtime is not None:
            autonomous_runtime.stop()
        voice.stop_speaking()
