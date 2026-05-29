import subprocess
from typing import Any

from app.io.stt import FasterWhisperTranscriber
from app.io.voice_config import VoiceConfig
from app.io.voice_input import VoiceInput
from app.io.voice_output import VoiceOutput, VoiceOutputError
from app.latency import DISABLED_LATENCY_LOGGER, LatencyLogger

__all__ = ["VoiceConfig", "VoiceIO", "VoiceOutputError"]


class VoiceIO:
    def __init__(
        self,
        config: VoiceConfig,
        transcriber: FasterWhisperTranscriber | None = None,
        latency: LatencyLogger | None = None,
    ) -> None:
        self.config = config
        self.latency = latency or DISABLED_LATENCY_LOGGER
        self.input = VoiceInput(config, transcriber=transcriber, latency=self.latency)
        self.output = VoiceOutput(config, latency=self.latency)

    @property
    def transcriber(self) -> FasterWhisperTranscriber | None:
        return self.input.transcriber

    @transcriber.setter
    def transcriber(self, value: FasterWhisperTranscriber | None) -> None:
        self.input.transcriber = value

    @property
    def playback_process(self) -> subprocess.Popen[str] | None:
        return self.output.playback_process

    @playback_process.setter
    def playback_process(self, value: subprocess.Popen[str] | None) -> None:
        self.output.playback_process = value

    def read_text(self) -> str:
        return self.input.read_text(self.stop_speaking)

    def read_voice_text(self) -> str:
        return self.input.read_voice_text(self.stop_speaking)

    def speak(self, text: str) -> None:
        self.output.speak(text)

    def stop_speaking(self) -> None:
        self.output.stop_speaking()

    def _wait_for_blocking_playback(self, process: subprocess.Popen[Any]) -> None:
        self.output.wait_for_blocking_playback(process)

    @staticmethod
    def _extract_transcript(stdout: str) -> str:
        return VoiceInput.extract_transcript(stdout)
