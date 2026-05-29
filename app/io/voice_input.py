import shutil
import subprocess
import sys
from collections.abc import Callable

from app.io.stt import FasterWhisperTranscriber
from app.io.voice_config import VoiceConfig
from app.latency import DISABLED_LATENCY_LOGGER, LatencyLogger
from app.text import sanitize_text


class VoiceInput:
    def __init__(
        self,
        config: VoiceConfig,
        transcriber: FasterWhisperTranscriber | None = None,
        latency: LatencyLogger | None = None,
    ) -> None:
        self.config = config
        self.transcriber = transcriber
        self.latency = latency or DISABLED_LATENCY_LOGGER

    def read_text(self, stop_speaking: Callable[[], None]) -> str:
        if not self.config.input_enabled:
            return sanitize_text(input("User: ")).strip()
        stop_speaking()
        self.latency.event("voice.read_text.start")
        if self.config.input_backend == "faster_whisper_inprocess":
            return self._read_text_inprocess()
        return self._read_text_command()

    def _read_text_inprocess(self) -> str:
        print("Listening...")
        if self.transcriber is None:
            self.transcriber = FasterWhisperTranscriber(self.config.stt_config, self.latency)
        try:
            text = sanitize_text(self.transcriber.record_and_transcribe()).strip()
        except Exception as exc:
            print(f"Voice input failed: {exc}")
            return sanitize_text(input("User: ")).strip()
        self.latency.event("voice.read_text.end")
        if text:
            print(f"User: {text}")
            return text
        print("User: ")
        return ""

    def _read_text_command(self) -> str:
        if not self.config.input_command:
            return sanitize_text(input("User: ")).strip()

        command = self.config.input_command
        executable = shutil.which(command[0])
        if executable is None:
            print(f"Voice input command not found: {command[0]}")
            return sanitize_text(input("User: ")).strip()

        print("Listening...")
        try:
            completed = subprocess.run(
                [executable, *command[1:]],
                text=True,
                capture_output=True,
                timeout=self.config.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"Voice input failed: {exc}")
            return sanitize_text(input("User: ")).strip()

        if completed.returncode != 0:
            detail = completed.stderr.strip() or f"exit={completed.returncode}"
            print(f"Voice input failed: {detail}")
            return sanitize_text(input("User: ")).strip()
        if self.latency.enabled and completed.stderr:
            print(completed.stderr, file=sys.stderr, end="")
        text = self.extract_transcript(completed.stdout)
        if text:
            print(f"User: {text}")
            self.latency.event("voice.read_text.end")
            return text
        print("User: ")
        self.latency.event("voice.read_text.end")
        return ""

    @staticmethod
    def extract_transcript(stdout: str) -> str:
        lines = [sanitize_text(line).strip() for line in stdout.splitlines()]
        return next((line for line in reversed(lines) if line), "")
