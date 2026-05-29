# ruff: noqa: F401,I001
from __future__ import annotations

import json
import tempfile
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
from scripts.stt_faster_whisper import RecordingState
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

    assert voice.read_text() == ""


def test_voice_input_can_use_inprocess_transcriber() -> None:
    config = VoiceConfig.from_profile(load_profile())
    config = replace(config, input_enabled=True, input_backend="faster_whisper_inprocess", output_enabled=False)
    voice = VoiceIO(config, transcriber=FakeTranscriber())  # type: ignore[arg-type]

    assert voice.read_text() == "オービット、予定を確認して"


def test_voice_config_reads_latency_related_voice_settings() -> None:
    config = VoiceConfig.from_profile(load_profile())

    assert config.blocking_playback is True
    assert config.input_backend == "command"
    assert config.stt_config.min_seconds == 0.5
    assert config.stt_config.silence_seconds == 0.45


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


def test_sentence_chunker_flushes_sentence_and_keeps_short_prefix() -> None:
    chunker = SentenceChunker(min_chars=5, max_chars=20)

    assert chunker.add("短い") == []
    assert chunker.add("文章です。次") == ["短い文章です。"]
    assert chunker.flush() == "次"

