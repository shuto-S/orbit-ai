import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from os import getenv
from pathlib import Path
from typing import Any

from app.text import sanitize_text


@dataclass(frozen=True)
class VoiceConfig:
    input_enabled: bool
    output_enabled: bool
    output_engine: str
    input_command: list[str]
    output_command: list[str]
    output_voice: str | None
    voicevox_url: str
    voicevox_speaker: int
    voicevox_player: list[str]
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
            input_command=[str(item) for item in input_config.get("command", [])],
            output_command=[str(item) for item in output_config.get("command", ["say"])],
            output_voice=output_config.get("voice"),
            voicevox_url=str(output_config.get("voicevox_url", "http://127.0.0.1:50021")),
            voicevox_speaker=int(output_config.get("speaker", 3)),
            voicevox_player=[str(item) for item in output_config.get("player", ["afplay"])],
            timeout_seconds=int(input_config.get("timeout_seconds", 30)),
        )


class VoiceIO:
    def __init__(self, config: VoiceConfig) -> None:
        self.config = config

    def read_text(self) -> str:
        if not self.config.input_enabled:
            return sanitize_text(input("User: ")).strip()
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
        text = self._extract_transcript(completed.stdout)
        if text:
            print(f"User: {text}")
            return text
        print("User: ")
        return ""

    def speak(self, text: str) -> None:
        if not self.config.output_enabled:
            return
        if self.config.output_engine == "voicevox":
            self._speak_voicevox(text)
            return
        self._speak_command(text)

    def _speak_command(self, text: str) -> None:
        if not self.config.output_command:
            return
        command = self.config.output_command
        executable = shutil.which(command[0])
        if executable is None:
            print(f"Voice output command not found: {command[0]}")
            return
        full_command = [executable, *command[1:]]
        if command[0] == "say" and self.config.output_voice:
            full_command.extend(["-v", str(self.config.output_voice)])
        full_command.append(text)
        try:
            subprocess.run(
                full_command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except OSError as exc:
            print(f"Voice output failed: {exc}")

    def _speak_voicevox(self, text: str) -> None:
        player = self.config.voicevox_player
        if not player:
            print("VOICEVOX player is not configured.")
            return
        executable = shutil.which(player[0])
        if executable is None:
            print(f"VOICEVOX player command not found: {player[0]}")
            return

        try:
            wav_path = self._synthesize_voicevox(text)
        except VoiceOutputError as exc:
            print(f"VOICEVOX output failed: {exc}")
            return

        try:
            subprocess.run(
                [executable, *player[1:], str(wav_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except OSError as exc:
            print(f"VOICEVOX playback failed: {exc}")

    def _synthesize_voicevox(self, text: str) -> Path:
        base_url = self.config.voicevox_url.rstrip("/")
        query_params = urllib.parse.urlencode({"text": text, "speaker": self.config.voicevox_speaker})
        query_request = urllib.request.Request(
            f"{base_url}/audio_query?{query_params}",
            method="POST",
        )
        try:
            with urllib.request.urlopen(query_request, timeout=10) as response:
                audio_query = response.read()
        except (OSError, urllib.error.URLError) as exc:
            raise VoiceOutputError(f"audio_query failed: {exc}") from exc

        synthesis_params = urllib.parse.urlencode({"speaker": self.config.voicevox_speaker})
        synthesis_request = urllib.request.Request(
            f"{base_url}/synthesis?{synthesis_params}",
            data=audio_query,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(synthesis_request, timeout=30) as response:
                wav_bytes = response.read()
        except (OSError, urllib.error.URLError) as exc:
            raise VoiceOutputError(f"synthesis failed: {exc}") from exc

        wav_file = tempfile.NamedTemporaryFile(prefix="orbit-ai-voicevox-", suffix=".wav", delete=False)
        with wav_file:
            wav_file.write(wav_bytes)
        return Path(wav_file.name)

    @staticmethod
    def _extract_transcript(stdout: str) -> str:
        lines = [sanitize_text(line).strip() for line in stdout.splitlines()]
        return next((line for line in reversed(lines) if line), "")


class VoiceOutputError(RuntimeError):
    pass
