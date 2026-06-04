import shutil
import subprocess
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from app.io.voice_config import VoiceConfig
from app.latency import DISABLED_LATENCY_LOGGER, LatencyLogger


class VoiceOutput:
    def __init__(self, config: VoiceConfig, latency: LatencyLogger | None = None) -> None:
        self.config = config
        self.latency = latency or DISABLED_LATENCY_LOGGER
        self.playback_process: subprocess.Popen[str] | None = None
        self._speech_lock = threading.Lock()
        self._speech_generation = 0

    def speak(self, text: str, wait: bool | None = None) -> None:
        if not self.config.output_enabled:
            return
        wait_for_playback = self.config.blocking_playback if wait is None else wait
        generation, previous_process = self._begin_speech()
        self._stop_process_if_running(previous_process)
        self._speak(text, wait=wait_for_playback, generation=generation)

    def speak_async(self, text: str) -> None:
        if not self.config.output_enabled:
            return
        generation, previous_process = self._begin_speech()
        self._stop_process_if_running(previous_process)
        threading.Thread(target=self._speak, args=(text, False, generation), daemon=True).start()

    def _speak(self, text: str, wait: bool, generation: int) -> None:
        if self.config.output_engine == "voicevox":
            self._speak_voicevox(text, wait=wait, generation=generation)
            return
        self._speak_command(text, wait=wait, generation=generation)

    def _speak_command(self, text: str, wait: bool, generation: int) -> None:
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
            if wait:
                self.wait_for_blocking_playback(process, generation=generation)
            else:
                if not self._set_playback_process(process, generation):
                    self.stop_process(process)
        except OSError as exc:
            print(f"Voice output failed: {exc}")

    def _speak_voicevox(self, text: str, wait: bool, generation: int) -> None:
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
        if not self._is_current_generation(generation):
            wav_path.unlink(missing_ok=True)
            return

        try:
            self.latency.event("voice.playback.start")
            process = subprocess.Popen(
                [executable, *player[1:], str(wav_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if wait:
                try:
                    self.wait_for_blocking_playback(process, generation=generation)
                finally:
                    wav_path.unlink(missing_ok=True)
                    self.latency.event("voice.playback.end")
                return
            if not self._set_playback_process(process, generation):
                self.stop_process(process)
                wav_path.unlink(missing_ok=True)
                self.latency.event("voice.playback.end")
                return
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
        process = self._cancel_speech()
        if process is None:
            return
        if process.poll() is not None:
            return
        self.stop_process(process)

    def wait_for_blocking_playback(self, process: subprocess.Popen[Any], generation: int | None = None) -> None:
        if generation is None:
            with self._speech_lock:
                self.playback_process = process
        elif not self._set_playback_process(process, generation):
            self.stop_process(process)
            return
        try:
            process.wait()
        except KeyboardInterrupt:
            self.stop_process(process)
            raise
        finally:
            self._clear_playback_process(process)

    def _begin_speech(self) -> tuple[int, subprocess.Popen[str] | None]:
        with self._speech_lock:
            self._speech_generation += 1
            generation = self._speech_generation
            process = self.playback_process
            self.playback_process = None
        return generation, process

    def _cancel_speech(self) -> subprocess.Popen[str] | None:
        with self._speech_lock:
            self._speech_generation += 1
            process = self.playback_process
            self.playback_process = None
        return process

    def _is_current_generation(self, generation: int) -> bool:
        with self._speech_lock:
            return generation == self._speech_generation

    def _set_playback_process(self, process: subprocess.Popen[Any], generation: int) -> bool:
        with self._speech_lock:
            if generation != self._speech_generation:
                return False
            self.playback_process = process
            return True

    def _clear_playback_process(self, process: subprocess.Popen[Any]) -> None:
        with self._speech_lock:
            if self.playback_process is process:
                self.playback_process = None

    @staticmethod
    def stop_process(process: subprocess.Popen[Any]) -> None:
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
            self._clear_playback_process(process)
            self.latency.event("voice.playback.end")

        threading.Thread(target=worker, daemon=True).start()

    def _stop_process_if_running(self, process: subprocess.Popen[str] | None) -> None:
        if process is None:
            return
        if process.poll() is None:
            self.stop_process(process)


class VoiceOutputError(RuntimeError):
    pass
