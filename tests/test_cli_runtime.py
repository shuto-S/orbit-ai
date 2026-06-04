from __future__ import annotations

import io
import sys
from types import SimpleNamespace
from typing import Any

from app.cli.progress import AgentProgressDisplay
from app.cli.runtime import run_terminal_loop
from app.session.state import SessionState


class FakeManager:
    def __init__(self) -> None:
        self.session_id: str | None = None
        self.state = SessionState.WAITING_FOR_NEXT_TURN
        self.handled_inputs: list[str] = []

    def start_conversation(self) -> SimpleNamespace:
        self.session_id = "session-1"
        return SimpleNamespace(text="こんにちは。何から始めますか？", session_id=self.session_id)

    def handle_input(self, text: str, progress_callback: Any | None = None) -> SimpleNamespace:
        self.handled_inputs.append(text)
        if progress_callback is not None:
            progress_callback("Codexから回答トークンを受信しています...")
        return SimpleNamespace(text="受け取りました。", session_id=self.session_id)


class FakeVoice:
    def __init__(self) -> None:
        self.config = SimpleNamespace(input_enabled=False)
        self.spoken: list[str] = []
        self.stopped = False

    def speak(self, text: str) -> None:
        self.spoken.append(text)

    def stop_speaking(self) -> None:
        self.stopped = True


class FakeLatency:
    def start_turn(self, session_id: str | None) -> None:
        pass

    def event(self, name: str) -> None:
        pass

    def bind_session(self, session_id: str | None) -> None:
        pass


class FakeTtyStream(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_terminal_loop_speaks_natural_acknowledgement_before_response(
    monkeypatch: Any,
    capsys: Any,
) -> None:
    stdin = io.StringIO("相談したい\n/quit\n")
    manager = FakeManager()
    voice = FakeVoice()

    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr("app.cli.runtime._is_interactive_stdin", lambda: False)
    monkeypatch.setattr(
        "app.cli.runtime.select.select",
        lambda readers, _write, _error, _timeout: (readers, [], []),
    )

    run_terminal_loop(
        manager,  # type: ignore[arg-type]
        voice,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        FakeLatency(),  # type: ignore[arg-type]
        check_interval_seconds=1,
    )

    stdout = capsys.readouterr().out
    assert manager.handled_inputs == ["相談したい"]
    assert "AI: 確認しますね。" in stdout
    assert "AI: Codexから回答トークンを受信しています..." in stdout
    assert "AI: 考えています..." not in stdout
    assert voice.spoken == ["こんにちは。何から始めますか？", "確認しますね。", "受け取りました。", "終了します。"]


def test_agent_progress_display_updates_inline_for_tty() -> None:
    stream = FakeTtyStream()

    with AgentProgressDisplay(stream=stream) as progress:
        progress.show("会話の文脈を確認しています...")
        progress.show("LLMに問い合わせています...")

    output = stream.getvalue()
    assert "\rAI: 会話の文脈を確認しています..." in output
    assert "\rAI: LLMに問い合わせています..." in output
    assert output.endswith("\r\033[K")
