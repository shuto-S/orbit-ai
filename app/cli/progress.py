from __future__ import annotations

import sys
from typing import TextIO


class AgentProgressDisplay:
    def __init__(
        self,
        stream: TextIO | None = None,
    ) -> None:
        self.stream = stream or sys.stdout
        self._inline_started = False
        self._last_message = ""

    def __enter__(self) -> AgentProgressDisplay:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.stop()

    def show(self, message: str) -> None:
        text = message.strip()
        if not text or text == self._last_message:
            return
        self._last_message = text
        if self._is_interactive_stream():
            self.stream.write(f"\rAI: {text}\033[K")
            self.stream.flush()
            self._inline_started = True
            return
        self.stream.write(f"AI: {text}\n")
        self.stream.flush()

    def stop(self) -> None:
        if self._inline_started:
            self.stream.write("\r\033[K")
            self.stream.flush()
            self._inline_started = False

    def _is_interactive_stream(self) -> bool:
        isatty = getattr(self.stream, "isatty", lambda: False)
        return bool(isatty())
