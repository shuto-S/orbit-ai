import select
import sys
import time
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
    handle_pet_command,
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
from app.ui.pet import PetUI

DEFAULT_PROACTIVE_CHECK_INTERVAL_SECONDS = 30
THINKING_ACKNOWLEDGEMENT = "確認しますね。"
VOICE_INPUT_COMMANDS = {"/v", "/voice"}


def proactive_check_interval_seconds(proactive_config: dict[str, object]) -> int:
    try:
        interval = int(proactive_config.get("check_interval_seconds", DEFAULT_PROACTIVE_CHECK_INTERVAL_SECONDS))
    except (TypeError, ValueError):
        return DEFAULT_PROACTIVE_CHECK_INTERVAL_SECONDS
    return max(1, interval)


def maybe_start_proactive_permission(
    manager: SessionManager,
    voice: VoiceIO,
    leading_newline: bool = False,
    pet_ui: PetUI | None = None,
) -> bool:
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
        _pet_say(pet_ui, output.text, state="notifying")
    return True


def announce_shutdown(voice: VoiceIO, leading_newline: bool = True, pet_ui: PetUI | None = None) -> None:
    if leading_newline:
        print()
    print("AI: 終了します。")
    _pet_say(pet_ui, "終了します。", state="idle")
    try:
        voice.speak("終了します。")
    except KeyboardInterrupt:
        voice.stop_speaking()


def read_text_with_idle_ticks(
    voice: VoiceIO,
    check_interval_seconds: int,
    on_idle_tick: Callable[[], bool],
    pet_ui: PetUI | None = None,
) -> str:
    if _is_interactive_stdin():
        if pet_ui is None or not _stdin_supports_select():
            return _read_interactive_text(voice, on_idle_tick, pet_ui)
        return _read_polling_text(voice, check_interval_seconds, on_idle_tick, pet_ui, poll_seconds=0.25)

    return _read_polling_text(voice, check_interval_seconds, on_idle_tick, pet_ui, poll_seconds=check_interval_seconds)


def _read_polling_text(
    voice: VoiceIO,
    check_interval_seconds: int,
    on_idle_tick: Callable[[], bool],
    pet_ui: PetUI | None,
    poll_seconds: float,
) -> str:
    prompt_shown = False
    last_idle_tick = time.monotonic()
    while True:
        if not prompt_shown:
            sys.stdout.write("User: ")
            sys.stdout.flush()
            prompt_shown = True
        pet_text = _pop_pet_submitted_text(pet_ui)
        if pet_text is not None:
            _echo_pet_user_text(pet_text, prompt_shown)
            return pet_text

        timeout = max(0.05, min(float(check_interval_seconds), poll_seconds))
        readable, _, _ = select.select([sys.stdin], [], [], timeout)
        if not readable:
            now = time.monotonic()
            should_tick = poll_seconds >= check_interval_seconds or now - last_idle_tick >= check_interval_seconds
            if should_tick and on_idle_tick():
                sys.stdout.write("User: ")
                sys.stdout.flush()
                prompt_shown = True
            if should_tick:
                last_idle_tick = now
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


def _stdin_supports_select() -> bool:
    try:
        sys.stdin.fileno()
    except (AttributeError, OSError, TypeError):
        return False
    return True


def _read_interactive_text(
    voice: VoiceIO,
    on_idle_tick: Callable[[], bool],
    pet_ui: PetUI | None = None,
) -> str:
    pet_text = _pop_pet_submitted_text(pet_ui)
    if pet_text is not None:
        _echo_pet_user_text(pet_text, prompt_shown=False)
        return pet_text
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


def _pop_pet_submitted_text(pet_ui: PetUI | None) -> str | None:
    if pet_ui is None:
        return None
    pop = getattr(pet_ui, "pop_submitted_text", None)
    if pop is None:
        return None
    text = sanitize_text(str(pop() or "")).strip()
    return text or None


def _echo_pet_user_text(text: str, prompt_shown: bool) -> None:
    if prompt_shown:
        sys.stdout.write(f"{text}\n")
    else:
        sys.stdout.write(f"User: {text}\n")
    sys.stdout.flush()


def run_terminal_loop(
    manager: SessionManager,
    voice: VoiceIO,
    store: MemoryStore,
    latency: LatencyLogger,
    check_interval_seconds: int,
    autonomous_runtime: AutonomousRuntime | None = None,
    pet_ui: PetUI | None = None,
) -> None:
    try:
        if pet_ui is not None:
            pet_ui.start()
        if autonomous_runtime is not None:
            autonomous_runtime.start()
        startup_output = manager.start_conversation()
        if startup_output.text:
            print(f"AI: {startup_output.text}")
            voice.speak_async(startup_output.text)
            _pet_say(pet_ui, startup_output.text, state="speaking")
        if autonomous_runtime is not None:
            autonomous_runtime.run_once()

        while True:
            latency.start_turn(session_id=manager.session_id)
            try:
                user_text = read_text_with_idle_ticks(
                    voice,
                    check_interval_seconds,
                    lambda: maybe_start_proactive_permission(
                        manager,
                        voice,
                        leading_newline=True,
                        pet_ui=pet_ui,
                    ),
                    pet_ui=pet_ui,
                )
            except EOFError:
                announce_shutdown(voice, pet_ui=pet_ui)
                break

            if not user_text:
                continue
            voice.stop_speaking()
            if user_text == "/quit":
                announce_shutdown(voice, leading_newline=False, pet_ui=pet_ui)
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
            if user_text == "/pet" or user_text.startswith("/pet "):
                handle_pet_command(pet_ui, user_text)
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
                    _pet_say(pet_ui, output.text, state="speaking")
                continue
            if user_text == "/proactive":
                handle_proactive_command(manager, voice)
                continue

            print(f"AI: {THINKING_ACKNOWLEDGEMENT}")
            voice.speak_async(THINKING_ACKNOWLEDGEMENT)
            _pet_say(pet_ui, THINKING_ACKNOWLEDGEMENT, state="thinking")
            latency.event("manager.handle_input.start")
            with AgentProgressDisplay() as progress:
                output = manager.handle_input(
                    user_text,
                    progress_callback=_progress_callback(progress, pet_ui),
                )
            latency.bind_session(output.session_id)
            latency.event("manager.handle_input.end")
            if output.text:
                print(f"AI: {output.text}")
                voice.speak_async(output.text)
                _pet_say(pet_ui, output.text, state="speaking")
            if autonomous_runtime is not None:
                autonomous_runtime.deliver_pending()
    except KeyboardInterrupt:
        announce_shutdown(voice)
    finally:
        if autonomous_runtime is not None:
            autonomous_runtime.stop()
        if pet_ui is not None:
            pet_ui.stop()
        voice.stop_speaking()


def _pet_say(pet_ui: PetUI | None, text: str | None, state: str = "speaking") -> None:
    if pet_ui is not None and text:
        pet_ui.say(text, state=state)


def _progress_callback(progress: AgentProgressDisplay, pet_ui: PetUI | None) -> Callable[[str], None]:
    def show(message: str) -> None:
        progress.show(message)
        if pet_ui is not None:
            pet_ui.progress(message)

    return show
