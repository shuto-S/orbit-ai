from dataclasses import dataclass
from pathlib import Path

from app.latency import DISABLED_LATENCY_LOGGER, LatencyLogger
from scripts.stt_faster_whisper import record_with_sounddevice


@dataclass(frozen=True)
class SttConfig:
    model: str = "base"
    language: str = "ja"
    device: str = "cpu"
    compute_type: str = "int8"
    sample_rate: int = 16000
    max_seconds: float = 12.0
    min_seconds: float = 0.5
    silence_seconds: float = 0.8
    silence_threshold: float = 0.01
    noise_calibration_seconds: float = 0.0
    silence_threshold_multiplier: float = 2.5
    beam_size: int = 5
    best_of: int = 5
    temperature: float = 0.0
    initial_prompt: str = (
        "Orbit AI assistant. Japanese conversation. Frequent words: "
        "オービット, オル, VOICEVOX, GitHub, issue, pull request, PR, Codex, タスク, 予定, メモ."
    )
    hotwords: str = "オービット オル VOICEVOX GitHub issue pull request PR Codex タスク 予定 メモ"


class FasterWhisperTranscriber:
    def __init__(self, config: SttConfig, latency: LatencyLogger | None = None) -> None:
        from faster_whisper import WhisperModel

        self.config = config
        self.latency = latency or DISABLED_LATENCY_LOGGER
        self.model = WhisperModel(config.model, device=config.device, compute_type=config.compute_type)

    def transcribe_file(self, path: Path) -> str:
        self.latency.event("voice.transcribe.start")
        segments, _info = self.model.transcribe(
            str(path),
            language=self.config.language,
            vad_filter=True,
            beam_size=self.config.beam_size,
            best_of=self.config.best_of,
            temperature=self.config.temperature,
            initial_prompt=self.config.initial_prompt or None,
            hotwords=self.config.hotwords or None,
        )
        text = "".join(segment.text for segment in segments).strip()
        self.latency.event("voice.transcribe.end")
        return text

    def record_and_transcribe(self) -> str:
        self.latency.event("voice.record.start")
        audio_path = record_with_sounddevice(
            sample_rate=self.config.sample_rate,
            max_seconds=self.config.max_seconds,
            min_seconds=self.config.min_seconds,
            silence_seconds=self.config.silence_seconds,
            silence_threshold=self.config.silence_threshold,
            noise_calibration_seconds=self.config.noise_calibration_seconds,
            silence_threshold_multiplier=self.config.silence_threshold_multiplier,
        )
        self.latency.event("voice.record.end")
        try:
            return self.transcribe_file(audio_path)
        finally:
            audio_path.unlink(missing_ok=True)
