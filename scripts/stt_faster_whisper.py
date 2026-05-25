import argparse
import queue
import subprocess
import sys
import tempfile
import wave
from collections import deque
from collections.abc import Iterable
from os import getenv
from pathlib import Path

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Record audio and transcribe it with faster-whisper.")
    parser.add_argument("--model", default="base", help="faster-whisper model name or local path.")
    parser.add_argument("--language", default="ja", help="Language code for transcription.")
    parser.add_argument("--device", default="cpu", help="cpu, cuda, auto, or other faster-whisper device.")
    parser.add_argument("--compute-type", default="int8", help="faster-whisper compute type.")
    parser.add_argument("--audio-file", help="Transcribe an existing audio file instead of recording.")
    parser.add_argument("--sample-rate", type=int, default=16000, help="Recording sample rate.")
    parser.add_argument("--max-seconds", type=float, default=12.0, help="Maximum recording length.")
    parser.add_argument("--min-seconds", type=float, default=0.5, help="Minimum recording length before silence stop.")
    parser.add_argument("--silence-seconds", type=float, default=0.45, help="Silence duration that ends recording.")
    parser.add_argument("--silence-threshold", type=float, default=0.01, help="RMS threshold for speech detection.")
    parser.add_argument(
        "--record-command",
        nargs="+",
        help="Optional external recorder command. Use {output} as placeholder for the wav path.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        audio_path = Path(args.audio_file) if args.audio_file else record_audio(args)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    try:
        latency_event("voice.transcribe.start")
        model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type)
        segments, _info = model.transcribe(str(audio_path), language=args.language, vad_filter=True)
        text = "".join(segment.text for segment in segments).strip()
        latency_event("voice.transcribe.end")
    finally:
        if not args.audio_file:
            audio_path.unlink(missing_ok=True)
    print(text)


def record_audio(args: argparse.Namespace) -> Path:
    if args.record_command:
        return record_with_command(args.record_command)
    return record_with_sounddevice(
        sample_rate=args.sample_rate,
        max_seconds=args.max_seconds,
        min_seconds=args.min_seconds,
        silence_seconds=args.silence_seconds,
        silence_threshold=args.silence_threshold,
    )


def record_with_command(record_command: list[str]) -> Path:
    output = tempfile.NamedTemporaryFile(prefix="orbit-ai-recording-", suffix=".wav", delete=False)
    output.close()
    output_path = Path(output.name)
    command = [part.replace("{output}", str(output_path)) for part in record_command]
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        raise SystemExit(f"record command failed: exit={completed.returncode}")
    return output_path


def record_with_sounddevice(
    sample_rate: int,
    max_seconds: float,
    min_seconds: float,
    silence_seconds: float,
    silence_threshold: float,
) -> Path:
    audio_queue: queue.Queue[np.ndarray] = queue.Queue()
    block_size = int(sample_rate * 0.1)
    max_blocks = max(1, int(max_seconds * sample_rate / block_size))
    min_blocks = max(1, int(min_seconds * sample_rate / block_size))
    silence_blocks = max(1, int(silence_seconds * sample_rate / block_size))

    def callback(indata: np.ndarray, _frames: int, _time: object, status: sd.CallbackFlags) -> None:
        if status:
            print(f"Audio warning: {status}", file=sys.stderr, flush=True)
        audio_queue.put(indata.copy())

    latency_event("voice.record.start")
    print("Listening... speak now.", file=sys.stderr, flush=True)
    state = RecordingState(pre_roll_blocks=max(1, int(0.3 * sample_rate / block_size)))

    try:
        with sd.InputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="float32",
            blocksize=block_size,
            callback=callback,
        ):
            for block_index in range(max_blocks):
                chunk = audio_queue.get()
                should_stop = state.add_chunk(chunk, silence_threshold)
                if block_index >= min_blocks and state.heard_speech and state.trailing_silence >= silence_blocks:
                    should_stop = True
                if should_stop:
                    break
    except Exception as exc:
        raise RuntimeError(f"microphone recording failed: {exc}") from exc

    latency_event("voice.record.end")
    chunks = state.recorded_chunks()
    if not chunks:
        raise RuntimeError("microphone recording produced no speech")
    audio = np.concatenate(list(chunks), axis=0).reshape(-1)
    return write_wav(audio, sample_rate)


class RecordingState:
    def __init__(self, pre_roll_blocks: int) -> None:
        self.pre_roll: deque[np.ndarray] = deque(maxlen=pre_roll_blocks)
        self.chunks: list[np.ndarray] = []
        self.trailing_silence = 0
        self.heard_speech = False

    def add_chunk(self, chunk: np.ndarray, silence_threshold: float) -> bool:
        rms = calculate_rms(chunk)
        if not self.heard_speech:
            self.pre_roll.append(chunk)
            if rms < silence_threshold:
                return False
            self.heard_speech = True
            self.chunks.extend(self.pre_roll)
            self.pre_roll.clear()
            self.trailing_silence = 0
            return False
        self.chunks.append(chunk)
        if rms >= silence_threshold:
            self.trailing_silence = 0
        else:
            self.trailing_silence += 1
        return False

    def recorded_chunks(self) -> Iterable[np.ndarray]:
        if not self.heard_speech:
            return []
        return self.chunks


def calculate_rms(chunk: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(chunk))))


def latency_event(name: str) -> None:
    if getenv("ORBIT_AI_LATENCY_LOG") == "1":
        print(name, file=sys.stderr, flush=True)


def write_wav(audio: np.ndarray, sample_rate: int) -> Path:
    clipped = np.clip(audio, -1.0, 1.0)
    pcm = (clipped * 32767).astype(np.int16)
    output = tempfile.NamedTemporaryFile(prefix="orbit-ai-recording-", suffix=".wav", delete=False)
    output.close()
    output_path = Path(output.name)
    with wave.open(str(output_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())
    return output_path


if __name__ == "__main__":
    main()
