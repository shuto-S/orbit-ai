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
            return self._read_keyboard_text()
        return self.read_voice_text(stop_speaking)

    def read_voice_text(self, stop_speaking: Callable[[], None]) -> str:
        if not self.config.input_enabled:
            print("Voice input is off.")
            return ""
        stop_speaking()
        self.latency.event("voice.read_text.start")
        if self.config.input_backend == "faster_whisper_inprocess":
            return self._read_text_inprocess()
        return self._read_text_command()

    def _read_keyboard_text(self) -> str:
        return self._sanitize_keyboard_line(input("User: "))

    def _read_text_inprocess(self) -> str:
        print("Listening...")
        if self.transcriber is None:
            self.transcriber = FasterWhisperTranscriber(self.config.stt_config, self.latency)
        try:
            text = sanitize_text(self.transcriber.record_and_transcribe()).strip()
        except Exception as exc:
            print(f"Voice input failed: {exc}")
            self.latency.event("voice.read_text.end")
            return ""
        self.latency.event("voice.read_text.end")
        if text:
            print(f"User: {text}")
            return text
        print("User: ")
        return ""

    def _read_text_command(self) -> str:
        if not self.config.input_command:
            print("Voice input command is not configured.")
            self.latency.event("voice.read_text.end")
            return ""

        command = self.config.input_command
        executable = shutil.which(command[0])
        if executable is None:
            print(f"Voice input command not found: {command[0]}")
            self.latency.event("voice.read_text.end")
            return ""

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
            self.latency.event("voice.read_text.end")
            return ""

        return self._complete_command_result(completed.returncode, completed.stdout, completed.stderr)

    def _complete_command_result(self, returncode: int, stdout: str, stderr: str) -> str:
        if returncode != 0:
            detail = stderr.strip() or f"exit={returncode}"
            print(f"Voice input failed: {detail}")
            self.latency.event("voice.read_text.end")
            return ""
        if self.latency.enabled and stderr:
            print(stderr, file=sys.stderr, end="")
        text = self.extract_transcript(stdout)
        if text:
            print(f"User: {text}")
            self.latency.event("voice.read_text.end")
            return text
        print("User: ")
        self.latency.event("voice.read_text.end")
        return ""

    @staticmethod
    def _sanitize_keyboard_line(line: str) -> str:
        return sanitize_text(line).strip()

    @staticmethod
    def extract_transcript(stdout: str) -> str:
        lines = [sanitize_text(line).strip() for line in stdout.splitlines()]
        return next((line for line in reversed(lines) if line), "")
