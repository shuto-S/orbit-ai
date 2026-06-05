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
        self.stop_calls = 0

    def speak(self, text: str) -> None:
        self.spoken.append(text)

    def speak_async(self, text: str) -> None:
        self.spoken.append(text)

    def stop_speaking(self) -> None:
        self.stopped = True
        self.stop_calls += 1


class FakeLatency:
    def start_turn(self, session_id: str | None) -> None:
        pass

    def event(self, name: str) -> None:
        pass

    def bind_session(self, session_id: str | None) -> None:
        pass


class FakePetUI:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.say_calls: list[tuple[str, str]] = []
        self.progress_calls: list[str] = []
        self.submitted_texts: list[str] = []

    def start(self) -> bool:
        self.started = True
        return True

    def say(self, text: str, state: str = "speaking") -> bool:
        self.say_calls.append((text, state))
        return True

    def progress(self, text: str) -> bool:
        self.progress_calls.append(text)
        return True

    def pop_submitted_text(self) -> str | None:
        if self.submitted_texts:
            return self.submitted_texts.pop(0)
        return None

    def stop(self) -> None:
        self.stopped = True


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
    assert voice.stop_calls >= 2


def test_terminal_loop_sends_speech_and_progress_to_pet_ui(
    monkeypatch: Any,
) -> None:
    stdin = io.StringIO("相談したい\n/quit\n")
    manager = FakeManager()
    voice = FakeVoice()
    pet_ui = FakePetUI()

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
        pet_ui=pet_ui,  # type: ignore[arg-type]
    )

    assert pet_ui.started
    assert pet_ui.stopped
    assert pet_ui.say_calls == [
        ("こんにちは。何から始めますか？", "speaking"),
        ("確認しますね。", "thinking"),
        ("受け取りました。", "speaking"),
        ("終了します。", "idle"),
    ]
    assert pet_ui.progress_calls == ["Codexから回答トークンを受信しています..."]


def test_terminal_loop_handles_pet_submitted_prompt(
    monkeypatch: Any,
) -> None:
    stdin = io.StringIO("/quit\n")
    manager = FakeManager()
    voice = FakeVoice()
    pet_ui = FakePetUI()
    pet_ui.submitted_texts.append("Petから相談したい")

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
        pet_ui=pet_ui,  # type: ignore[arg-type]
    )

    assert manager.handled_inputs == ["Petから相談したい"]


def test_agent_progress_display_updates_inline_for_tty() -> None:
    stream = FakeTtyStream()

    with AgentProgressDisplay(stream=stream) as progress:
        progress.show("会話の文脈を確認しています...")
        progress.show("LLMに問い合わせています...")

    output = stream.getvalue()
    assert "\rAI: 会話の文脈を確認しています..." in output
    assert "\rAI: LLMに問い合わせています..." in output
    assert output.endswith("\r\033[K")
