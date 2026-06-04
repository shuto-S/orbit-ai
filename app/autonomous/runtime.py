from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.autonomous.scheduler import AutonomousScheduler
from app.io.voice import VoiceIO
from app.memory.store import AutonomousNotification, MemoryStore
from app.session.manager import SessionManager

OutputCallback = Callable[[str], None]


class AutonomousRuntime:
    def __init__(
        self,
        store: MemoryStore,
        manager: SessionManager,
        voice: VoiceIO,
        config: dict[str, Any],
        scheduler: AutonomousScheduler | None = None,
        output: OutputCallback | None = None,
    ) -> None:
        self.store = store
        self.manager = manager
        self.voice = voice
        self.config = config
        self.scheduler = scheduler or AutonomousScheduler(
            store,
            retry_after_seconds=_int_config(config, "retry_after_seconds", 300),
        )
        self.output = output or _print_autonomous_output
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.config.get("enabled", True):
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def run_once(self, now: datetime | None = None) -> list[AutonomousNotification]:
        if not self.config.get("enabled", True):
            return []
        current = now or datetime.now(UTC)
        created = self.scheduler.tick(current)
        self.deliver_pending(current)
        return created

    def deliver_pending(self, now: datetime | None = None) -> list[AutonomousNotification]:
        if str(self.config.get("delivery_mode", "speak_when_idle")) != "speak_when_idle":
            return []
        current = now or datetime.now(UTC)
        delivered: list[AutonomousNotification] = []
        for notification in self.store.list_autonomous_notifications(status="pending", limit=5):
            output = self.manager.deliver_autonomous_notification(notification)
            if output is None or not output.text:
                break
            self.output(output.text)
            self.voice.speak_async(output.text)
            marked = self.store.mark_autonomous_notification_delivered(notification.id, current)
            if marked is not None:
                delivered.append(marked)
        return delivered

    def _loop(self) -> None:
        interval = _int_config(self.config, "tick_interval_seconds", 30)
        while not self._stop.wait(interval):
            self.run_once()


def _int_config(config: dict[str, Any], key: str, default: int) -> int:
    try:
        value = int(config.get(key, default))
    except (TypeError, ValueError):
        return default
    return max(1, value)


def _print_autonomous_output(text: str) -> None:
    print()
    print(f"AI: {text}")
