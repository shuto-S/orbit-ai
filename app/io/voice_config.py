from dataclasses import dataclass
from os import getenv
from typing import Any

from app.io.stt import SttConfig


@dataclass(frozen=True)
class VoiceConfig:
    input_enabled: bool
    output_enabled: bool
    output_engine: str
    input_backend: str
    input_command: list[str]
    stt_config: SttConfig
    output_command: list[str]
    output_voice: str | None
    voicevox_url: str
    voicevox_speaker: int
    voicevox_player: list[str]
    blocking_playback: bool
    timeout_seconds: int

    @classmethod
    def from_profile(cls, profile: dict[str, Any]) -> "VoiceConfig":
        voice = profile.get("voice", {})
        if not isinstance(voice, dict):
            voice = {}
        input_config = voice.get("input", {})
        output_config = voice.get("output", {})
        if not isinstance(input_config, dict):
            input_config = {}
        if not isinstance(output_config, dict):
            output_config = {}
        input_enabled = bool(input_config.get("enabled", False))
        output_enabled = bool(output_config.get("enabled", False))
        if getenv("ORBIT_AI_VOICE_INPUT") == "1":
            input_enabled = True
        if getenv("ORBIT_AI_VOICE_OUTPUT") == "1":
            output_enabled = True
        return cls(
            input_enabled=input_enabled,
            output_enabled=output_enabled,
            output_engine=str(output_config.get("engine", "say")),
            input_backend=str(input_config.get("backend", "command")),
            input_command=[str(item) for item in input_config.get("command", [])],
            stt_config=SttConfig(
                model=str(input_config.get("model", "base")),
                language=str(input_config.get("language", "ja")),
                device=str(input_config.get("device", "cpu")),
                compute_type=str(input_config.get("compute_type", "int8")),
                sample_rate=int(input_config.get("sample_rate", 16000)),
                max_seconds=float(input_config.get("max_seconds", 12.0)),
                min_seconds=float(input_config.get("min_seconds", 0.5)),
                silence_seconds=float(input_config.get("silence_seconds", 0.45)),
                silence_threshold=float(input_config.get("silence_threshold", 0.01)),
            ),
            output_command=[str(item) for item in output_config.get("command", ["say"])],
            output_voice=output_config.get("voice"),
            voicevox_url=str(output_config.get("voicevox_url", "http://127.0.0.1:50021")),
            voicevox_speaker=int(output_config.get("speaker", 3)),
            voicevox_player=[str(item) for item in output_config.get("player", ["afplay"])],
            blocking_playback=bool(output_config.get("blocking_playback", True)),
            timeout_seconds=int(input_config.get("timeout_seconds", 30)),
        )
