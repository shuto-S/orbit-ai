# ruff: noqa: F401,I001
from __future__ import annotations

import json
import tempfile
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any

import numpy as np
import pytest

from app.actions import ActionRequest, create_default_dispatcher
from app.ai.app_server_backend import AppServerCodexBackend, BackendResponse, CodexAppServerError
from app.ai.response_agent import CODEX_ERROR_PREFIX, ResponseAgent
from app.ai.streaming import SentenceChunker
from app.config.autonomy import AutonomyLevel, parse_autonomy_config
from app.config.loader import (
    load_autonomy_config,
    load_permission_policy_config,
    load_proactive_config,
    load_profile,
)
from app.config.permission_policy import (
    ActionPermissionPolicy,
    PermissionDecision,
    PermissionPolicyConfig,
    evaluate_permission,
    parse_permission_policy_config,
)
from app.io.stt import FasterWhisperTranscriber, SttConfig
from app.io.voice import VoiceConfig, VoiceIO
from app.latency import DEFAULT_LATENCY_LOG_PATH, LatencyLogger
from app.main import (
    DEFAULT_PROACTIVE_CHECK_INTERVAL_SECONDS,
    announce_shutdown,
    handle_daily_command,
    handle_proactive_command,
    handle_task_command,
    maybe_start_proactive_permission,
    proactive_check_interval_seconds,
    read_text_with_idle_ticks,
    show_tasks,
)
from app.memory.store import MemoryStore, parse_due_at, utc_aware
from app.session.manager import SessionManager
from app.session.state import SessionState
from app.text import sanitize_text
from scripts.latency_summary import percentile, read_events
from scripts.stt_faster_whisper import RecordingState, calibrated_silence_threshold
from tests.helpers.fakes import ErrorBackend, FakeBackend, FakeResponseAgent, FakeRpcClient, FakeTranscriber

def test_voice_io_extracts_last_non_empty_transcript_line() -> None:
    stdout = "Listening... speak now.\n\nオービットおはよう\n"

    assert VoiceIO._extract_transcript(stdout) == "オービットおはよう"


def test_voice_input_empty_transcript_does_not_fallback_to_text_input(monkeypatch: pytest.MonkeyPatch) -> None:
    config = VoiceConfig.from_profile(load_profile())
    config = replace(config, input_enabled=True, input_command=["uv"], output_enabled=False)
    voice = VoiceIO(config)

    monkeypatch.setattr("shutil.which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: CompletedProcess(args=args, returncode=0, stdout="\n", stderr=""),
    )

    assert voice.read_voice_text() == ""


def test_text_input_is_primary_even_when_voice_input_is_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    config = VoiceConfig.from_profile(load_profile())
    config = replace(config, input_enabled=True, input_command=["uv"], output_enabled=False)
    voice = VoiceIO(config)

    class FakeStdin:
        def readline(self) -> str:
            return "今日のスケジュールは？\n"

    fake_stdin = FakeStdin()

    def unexpected_voice_read() -> str:
        raise AssertionError("normal text input should not start voice recognition")

    voice.read_voice_text = unexpected_voice_read  # type: ignore[method-assign]
    monkeypatch.setattr("app.cli.runtime.sys.stdin", fake_stdin)
    monkeypatch.setattr("app.cli.runtime.select.select", lambda *_args: ([fake_stdin], [], []))

    assert read_text_with_idle_ticks(voice, 1, lambda: False) == "今日のスケジュールは？"


def test_voice_trigger_command_runs_voice_input(monkeypatch: pytest.MonkeyPatch) -> None:
    config = VoiceConfig.from_profile(load_profile())
    config = replace(config, input_enabled=True, input_command=["uv"], output_enabled=False)
    voice = VoiceIO(config)

    class FakeStdin:
        def readline(self) -> str:
            return "/v\n"

    fake_stdin = FakeStdin()
    voice.read_voice_text = lambda: "音声認識の結果"  # type: ignore[method-assign]
    monkeypatch.setattr("app.cli.runtime.sys.stdin", fake_stdin)
    monkeypatch.setattr("app.cli.runtime.select.select", lambda *_args: ([fake_stdin], [], []))

    assert read_text_with_idle_ticks(voice, 1, lambda: False) == "音声認識の結果"


def test_interactive_text_input_uses_standard_input_when_voice_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    config = VoiceConfig.from_profile(load_profile())
    config = replace(config, input_enabled=True, input_command=["uv"], output_enabled=False)
    voice = VoiceIO(config)

    class FakeTtyStdin:
        def isatty(self) -> bool:
            return True

    def unexpected_voice_read() -> str:
        raise AssertionError("interactive text input should not start voice recognition")

    voice.read_voice_text = unexpected_voice_read  # type: ignore[method-assign]
    monkeypatch.setattr("app.cli.runtime.sys.stdin", FakeTtyStdin())
    monkeypatch.setattr("builtins.input", lambda prompt: "未読メールある？？")

    assert read_text_with_idle_ticks(voice, 1, lambda: False) == "未読メールある？？"


def test_interactive_voice_trigger_command_runs_voice_input(monkeypatch: pytest.MonkeyPatch) -> None:
    config = VoiceConfig.from_profile(load_profile())
    config = replace(config, input_enabled=True, input_command=["uv"], output_enabled=False)
    voice = VoiceIO(config)

    class FakeTtyStdin:
        def isatty(self) -> bool:
            return True

    voice.read_voice_text = lambda: "音声認識の結果"  # type: ignore[method-assign]
    monkeypatch.setattr("app.cli.runtime.sys.stdin", FakeTtyStdin())
    monkeypatch.setattr("builtins.input", lambda prompt: "/voice")

    assert read_text_with_idle_ticks(voice, 1, lambda: False) == "音声認識の結果"


def test_voice_keyboard_line_preserves_japanese_ime_text() -> None:
    text = "  今日は15時からレビューお願いします。未確定のPRも確認して。\n"

    assert VoiceIO(VoiceConfig.from_profile(load_profile())).input._sanitize_keyboard_line(text) == (
        "今日は15時からレビューお願いします。未確定のPRも確認して。"
    )


def test_voice_input_can_use_inprocess_transcriber() -> None:
    config = VoiceConfig.from_profile(load_profile())
    config = replace(config, input_enabled=True, input_backend="faster_whisper_inprocess", output_enabled=False)
    voice = VoiceIO(config, transcriber=FakeTranscriber())  # type: ignore[arg-type]

    assert voice.read_text() == "オービット、予定を確認して"


def test_inprocess_transcriber_passes_accuracy_options_to_whisper() -> None:
    class Segment:
        text = " オービットです"

    class Model:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] = {}

        def transcribe(self, path: str, **kwargs: object) -> tuple[list[Segment], object]:
            self.kwargs = {"path": path, **kwargs}
            return [Segment()], object()

    model = Model()
    transcriber = FasterWhisperTranscriber.__new__(FasterWhisperTranscriber)
    transcriber.config = SttConfig(
        beam_size=7,
        best_of=3,
        temperature=0.2,
        initial_prompt="オービット",
        hotwords="オービット Codex",
    )
    transcriber.latency = LatencyLogger(enabled=False)
    transcriber.model = model

    assert transcriber.transcribe_file(Path("sample.wav")) == "オービットです"
    assert model.kwargs["path"] == "sample.wav"
    assert model.kwargs["beam_size"] == 7
    assert model.kwargs["best_of"] == 3
    assert model.kwargs["temperature"] == 0.2
    assert model.kwargs["initial_prompt"] == "オービット"
    assert model.kwargs["hotwords"] == "オービット Codex"


def test_voice_config_reads_latency_related_voice_settings() -> None:
    config = VoiceConfig.from_profile(load_profile())

    assert config.blocking_playback is True
    assert config.input_backend == "command"
    assert config.stt_config.model == "base"
    assert config.stt_config.min_seconds == 0.5
    assert config.stt_config.silence_seconds == 0.8
    assert config.stt_config.noise_calibration_seconds == 0.0
    assert config.stt_config.silence_threshold_multiplier == 2.5
    assert config.stt_config.beam_size == 5
    assert config.stt_config.best_of == 5
    assert config.stt_config.temperature == 0.0
    assert "オービット" in config.stt_config.initial_prompt
    assert "オービット" in config.stt_config.hotwords


def test_voice_stop_speaking_without_process_is_noop() -> None:
    voice = VoiceIO(VoiceConfig.from_profile(load_profile()))

    voice.stop_speaking()


def test_voice_blocking_playback_interrupt_stops_process() -> None:
    class InterruptingPlaybackProcess:
        def __init__(self) -> None:
            self.terminated = False
            self.wait_calls: list[float | None] = []

        def wait(self, timeout: float | None = None) -> int:
            self.wait_calls.append(timeout)
            if timeout is None:
                raise KeyboardInterrupt
            return 0

        def poll(self) -> int | None:
            return None

        def terminate(self) -> None:
            self.terminated = True

    voice = VoiceIO(VoiceConfig.from_profile(load_profile()))
    process = InterruptingPlaybackProcess()

    with pytest.raises(KeyboardInterrupt):
        voice._wait_for_blocking_playback(process)  # type: ignore[arg-type]

    assert process.terminated is True
    assert process.wait_calls == [None, 0.5]
    assert voice.playback_process is None


def test_voice_speak_async_does_not_wait_for_playback_even_when_blocking_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class PlaybackProcess:
        def __init__(self) -> None:
            self.wait_calls: list[float | None] = []

        def wait(self, timeout: float | None = None) -> int:
            self.wait_calls.append(timeout)
            return 0

        def poll(self) -> int | None:
            return None

        def terminate(self) -> None:
            pass

        def kill(self) -> None:
            pass

    process = PlaybackProcess()
    config = replace(
        VoiceConfig.from_profile(load_profile()),
        output_enabled=True,
        output_engine="say",
        output_command=["say"],
        blocking_playback=True,
    )
    voice = VoiceIO(config)

    monkeypatch.setattr("shutil.which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr("subprocess.Popen", lambda *_args, **_kwargs: process)

    voice.speak_async("長い回答です。")

    assert _wait_until(lambda: voice.playback_process is process)
    assert process.wait_calls == []


def test_voice_stop_speaking_cancels_pending_async_voicevox_playback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    synth_started = threading.Event()
    synth_release = threading.Event()
    synth_finished = threading.Event()
    popen_calls: list[object] = []
    wav_path = tmp_path / "voice.wav"

    config = replace(
        VoiceConfig.from_profile(load_profile()),
        output_enabled=True,
        output_engine="voicevox",
        voicevox_player=["afplay"],
        blocking_playback=True,
    )
    voice = VoiceIO(config)

    def synthesize(_text: str) -> Path:
        synth_started.set()
        assert synth_release.wait(timeout=1)
        wav_path.write_bytes(b"RIFF")
        synth_finished.set()
        return wav_path

    monkeypatch.setattr("shutil.which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(voice.output, "_synthesize_voicevox", synthesize)
    monkeypatch.setattr("subprocess.Popen", lambda *args, **kwargs: popen_calls.append((args, kwargs)))

    voice.speak_async("古い回答です。")
    assert synth_started.wait(timeout=1)

    voice.stop_speaking()
    synth_release.set()

    assert synth_finished.wait(timeout=1)
    time.sleep(0.05)
    assert popen_calls == []
    assert wav_path.exists() is False


def test_announce_shutdown_suppresses_interrupt_during_voice_output(capsys: pytest.CaptureFixture[str]) -> None:
    class InterruptingVoice:
        def __init__(self) -> None:
            self.stopped = False
            self.spoken: list[str] = []

        def speak(self, text: str) -> None:
            self.spoken.append(text)
            raise KeyboardInterrupt

        def stop_speaking(self) -> None:
            self.stopped = True

    voice = InterruptingVoice()

    announce_shutdown(voice, leading_newline=False)  # type: ignore[arg-type]

    assert capsys.readouterr().out == "AI: 終了します。\n"
    assert voice.spoken == ["終了します。"]
    assert voice.stopped is True


def test_recording_state_keeps_only_pre_roll_before_speech() -> None:
    state = RecordingState(pre_roll_blocks=2)
    silence = np.zeros((2, 1), dtype=np.float32)
    speech = np.ones((2, 1), dtype=np.float32)

    state.add_chunk(silence, silence_threshold=0.5)
    state.add_chunk(silence, silence_threshold=0.5)
    state.add_chunk(speech, silence_threshold=0.5)

    chunks = list(state.recorded_chunks())
    assert len(chunks) == 2
    assert chunks[0] is silence
    assert chunks[-1] is speech


def test_calibrated_silence_threshold_uses_noise_floor_without_lowering_minimum() -> None:
    quiet = [np.full((2, 1), 0.001, dtype=np.float32)]
    noisy = [np.full((2, 1), 0.02, dtype=np.float32)]

    assert calibrated_silence_threshold(quiet, minimum_threshold=0.01, multiplier=2.5) == pytest.approx(0.01)
    assert calibrated_silence_threshold(noisy, minimum_threshold=0.01, multiplier=2.5) == pytest.approx(0.05)


def test_sentence_chunker_flushes_sentence_and_keeps_short_prefix() -> None:
    chunker = SentenceChunker(min_chars=5, max_chars=20)

    assert chunker.add("短い") == []
    assert chunker.add("文章です。次") == ["短い文章です。"]
    assert chunker.flush() == "次"


def _wait_until(predicate: Any, timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()
