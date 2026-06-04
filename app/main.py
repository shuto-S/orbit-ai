from app.cli.commands import (
    handle_approval_command,
    handle_daily_command,
    handle_draft_command,
    handle_loop_command,
    handle_memory_command,
    handle_proactive_command,
    handle_task_command,
)
from app.cli.display import (
    print_banner,
    show_approval_requests,
    show_draft_detail,
    show_drafts,
    show_memory,
    show_open_loops,
    show_tasks,
)
from app.cli.options import apply_cli_options, parse_cli_options
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
    "handle_approval_command",
    "handle_daily_command",
    "handle_draft_command",
    "handle_loop_command",
    "handle_memory_command",
    "handle_proactive_command",
    "handle_task_command",
    "main",
    "maybe_start_proactive_permission",
    "proactive_check_interval_seconds",
    "read_text_with_idle_ticks",
    "show_approval_requests",
    "show_draft_detail",
    "show_drafts",
    "show_memory",
    "show_open_loops",
    "show_tasks",
]


def main(argv: list[str] | None = None) -> None:
    options = parse_cli_options(argv)
    profile = apply_cli_options(load_profile(), options)
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
