from app.cli.commands import (
    handle_daily_command,
    handle_loop_command,
    handle_memory_command,
    handle_proactive_command,
    handle_task_command,
)
from app.cli.display import print_banner, show_memory, show_open_loops, show_tasks
from app.cli.runtime import (
    DEFAULT_PROACTIVE_CHECK_INTERVAL_SECONDS,
    announce_shutdown,
    maybe_start_proactive_permission,
    proactive_check_interval_seconds,
    read_text_with_idle_ticks,
    run_terminal_loop,
)
from app.config.loader import load_autonomy_config, load_proactive_config, load_profile
from app.io.voice import VoiceConfig, VoiceIO
from app.latency import LatencyLogger
from app.memory.store import MemoryStore
from app.session.manager import SessionManager

__all__ = [
    "DEFAULT_PROACTIVE_CHECK_INTERVAL_SECONDS",
    "announce_shutdown",
    "handle_daily_command",
    "handle_loop_command",
    "handle_memory_command",
    "handle_proactive_command",
    "handle_task_command",
    "main",
    "maybe_start_proactive_permission",
    "proactive_check_interval_seconds",
    "read_text_with_idle_ticks",
    "show_memory",
    "show_open_loops",
    "show_tasks",
]


def main() -> None:
    profile = load_profile()
    proactive_config = load_proactive_config()
    autonomy_config = load_autonomy_config(profile)
    check_interval_seconds = proactive_check_interval_seconds(proactive_config)
    latency = LatencyLogger.from_profile(profile)
    store = MemoryStore()
    manager = SessionManager(
        profile,
        proactive_config,
        store,
        autonomy_config=autonomy_config,
        latency=latency,
        start_without_wake_word=True,
    )
    voice = VoiceIO(VoiceConfig.from_profile(profile), latency=latency)
    print_banner(manager, voice.config)
    run_terminal_loop(manager, voice, store, latency, check_interval_seconds)


if __name__ == "__main__":
    main()
