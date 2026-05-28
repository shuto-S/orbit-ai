import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from os import getenv
from pathlib import Path
from typing import Any

from app.io.stt import FasterWhisperTranscriber, SttConfig
from app.latency import DISABLED_LATENCY_LOGGER, LatencyLogger
from app.text import sanitize_text


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


class VoiceIO:
    def __init__(
        self,
        config: VoiceConfig,
        transcriber: FasterWhisperTranscriber | None = None,
        latency: LatencyLogger | None = None,
    ) -> None:
        self.config = config
        self.latency = latency or DISABLED_LATENCY_LOGGER
        self.transcriber = transcriber
        self.playback_process: subprocess.Popen[str] | None = None

    def read_text(self) -> str:
        if not self.config.input_enabled:
            return sanitize_text(input("User: ")).strip()
        self.stop_speaking()
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
        text = self._extract_transcript(completed.stdout)
        if text:
            print(f"User: {text}")
            self.latency.event("voice.read_text.end")
            return text
        print("User: ")
        self.latency.event("voice.read_text.end")
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
            process = subprocess.Popen(full_command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if self.config.blocking_playback:
                self._wait_for_blocking_playback(process)
            else:
                self.playback_process = process
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
            self.latency.event("voice.synthesis.start")
            wav_path = self._synthesize_voicevox(text)
            self.latency.event("voice.synthesis.end")
        except VoiceOutputError as exc:
            print(f"VOICEVOX output failed: {exc}")
            return

        try:
            self.latency.event("voice.playback.start")
            process = subprocess.Popen(
                [executable, *player[1:], str(wav_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if self.config.blocking_playback:
                try:
                    self._wait_for_blocking_playback(process)
                finally:
                    wav_path.unlink(missing_ok=True)
                    self.latency.event("voice.playback.end")
                return
            self.playback_process = process
            self._delete_after_playback(process, wav_path)
        except OSError as exc:
            print(f"VOICEVOX playback failed: {exc}")
            wav_path.unlink(missing_ok=True)

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

    def stop_speaking(self) -> None:
        process = self.playback_process
        if process is None:
            return
        if process.poll() is not None:
            self.playback_process = None
            return
        self._stop_process(process)
        self.playback_process = None

    def _wait_for_blocking_playback(self, process: subprocess.Popen[Any]) -> None:
        self.playback_process = process
        try:
            process.wait()
        except KeyboardInterrupt:
            self._stop_process(process)
            raise
        finally:
            if self.playback_process is process:
                self.playback_process = None

    @staticmethod
    def _stop_process(process: subprocess.Popen[Any]) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=0.5)

    def _delete_after_playback(self, process: subprocess.Popen[str], path: Path) -> None:
        def worker() -> None:
            process.wait()
            path.unlink(missing_ok=True)
            self.latency.event("voice.playback.end")

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _extract_transcript(stdout: str) -> str:
        lines = [sanitize_text(line).strip() for line in stdout.splitlines()]
        return next((line for line in reversed(lines) if line), "")


class VoiceOutputError(RuntimeError):
    pass
