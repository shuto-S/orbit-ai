# Orbit AI

Orbit AI is a terminal-based MVP for a voice-first personal secretary AI.

It starts a conversation when the user calls its name, continues naturally without requiring the name on every turn, and never ends a conversation without confirming with the user first.

## Setup

Requirements:

- Python 3.11+
- `uv`
- Docker, if you want VOICEVOX speech output through `make run`
- Codex CLI with `codex app-server` available

Run:

```sh
make run
```

Run in daemon mode:

```sh
make run-daemon
```

Stop VOICEVOX:

```sh
make stop-voice
```

## Usage

```text
User: オービット、相談したい
AI: ...

User: 今日の予定を整理したい
AI: ...

User: ありがとう
AI: この件はいったんここまでにしますか？

User: うん
AI: わかりました。また呼んでください。
```

Wake words are configured in `config/profile.json`. The default wake words include `オービット` and `オル`.

## Commands

- `/quit`: exit the app
- `/status`: show current state and session ID
- `/memory`: show saved memories and recent summaries
- `/tasks`: show open and snoozed tasks
- `/task done <id>`: mark a task as done
- `/task snooze <id> <when>`: snooze a task and save `<when>` as its due time
- `/proactive`: check whether there is a proactive candidate
- `/reset`: discard the current session and return to idle

## Proactive Checks

Proactive behavior is configured in `config/proactive.json`.
When the app is idle in text input mode, stdin is polled every `proactive.check_interval_seconds` equivalent (`check_interval_seconds` in the JSON file) so the policy can run without waiting forever inside `input()`.
If the policy allows an intervention, Orbit first asks the existing permission prompt and records the `proposed` event in `proactive_events`; accepting or rejecting the prompt records the existing `accepted` or `rejected` events.
When the policy does not allow an intervention, the app stays silent.

Voice input still uses blocking STT reads. In voice mode, proactive policy checks run immediately before and after each read instead of adding a separate background thread.

## Voice I/O

`make run` starts VOICEVOX Engine and runs the app with both voice input and voice output enabled:

```sh
ORBIT_AI_VOICE_INPUT=1 ORBIT_AI_VOICE_OUTPUT=1 uv run python -m app.main
```

VOICEVOX is managed through `scripts/voicevox.sh`:

```sh
make run
make run-daemon
make status-voice
make logs-voice
make stop-voice
```

## Daemon Mode

`make run-daemon` runs `scripts/boot.sh`, which starts `make run` and restarts it after the app exits.

Stop it with `Ctrl-C` in the terminal running the daemon, or send `SIGTERM` to the `scripts/boot.sh` process.

Logs are appended to:

```text
logs/orbit-ai.log
```

The `logs/` directory is kept in the repository, but log files are ignored by git.

Daemon settings can be changed with environment variables:

- `ORBIT_AI_RESTART_DELAY`: seconds to wait before restart. Default: `5`
- `ORBIT_AI_LOG_FILE`: log file path. Default: `logs/orbit-ai.log`
- `ORBIT_AI_DAEMON_COMMAND`: command to run in the restart loop. Default: `make run`
- `ORBIT_AI_RESTART_HOOK`: optional shell command called after an app exit before sleeping. The hook receives `ORBIT_AI_EXIT_STATUS`.

Use `ORBIT_AI_DAEMON_COMMAND` and `ORBIT_AI_RESTART_HOOK` only with trusted local values; both are executed by the shell with the current user permissions. The daemon log may include spoken text, typed input, AI responses, and hook output, so rotate or delete `logs/orbit-ai.log` when needed.

Speech output uses VOICEVOX Engine by default. The app calls `/audio_query` and `/synthesis`, then plays the generated WAV file with `afplay`.

Speech input uses `scripts/stt_faster_whisper.py`. The script records from the local microphone with `python-sounddevice`, then transcribes with `faster-whisper`.

On macOS, allow microphone access for Terminal or iTerm on first use. The default model is `base` to keep latency reasonable. Use `small` or larger if you prefer accuracy over speed.

The default voice input settings are tuned for shorter turn latency:

```json
{
  "backend": "command",
  "model": "base",
  "language": "ja",
  "max_seconds": 12,
  "min_seconds": 0.5,
  "silence_seconds": 0.45,
  "silence_threshold": 0.01
}
```

You can also transcribe an existing file:

```sh
uv run python scripts/stt_faster_whisper.py --audio-file path/to/audio.wav
```

### In-Process STT

The default `command` backend keeps backward compatibility by launching `scripts/stt_faster_whisper.py` for each turn.

For lower latency after the first turn, you can opt into the in-process backend:

```json
{
  "voice": {
    "input": {
      "backend": "faster_whisper_inprocess"
    }
  }
}
```

This loads `WhisperModel` once and reuses it for later turns. The first model load can still take time.

### Playback Mode

VOICEVOX playback is blocking by default for compatibility:

```json
{
  "voice": {
    "output": {
      "blocking_playback": true
    }
  }
}
```

Set `blocking_playback` to `false` to start `afplay` and return immediately. `VoiceIO.stop_speaking()` can stop an active playback process, which is the basis for future barge-in support.

## Latency Logging

Latency logging is disabled by default. Enable it with:

```sh
ORBIT_AI_LATENCY_LOG=1 make run
```

By default, logs are written to stderr and JSONL is appended to `data/latency.jsonl`. Set `ORBIT_AI_LATENCY_LOG_PATH` to choose another JSONL path:

```sh
ORBIT_AI_LATENCY_LOG=1 ORBIT_AI_LATENCY_LOG_PATH=data/latency.jsonl make run
```

You can also enable it and set a JSONL path in `config/profile.json`:

```json
{
  "latency": {
    "enabled": true,
    "log_path": "data/latency.jsonl"
  }
}
```

`ORBIT_AI_LATENCY_LOG_PATH` takes precedence over `latency.log_path`.

stderr logs keep the human-readable format and include events such as `voice.read_text.start`, `voice.record.start`, `voice.transcribe.end`, `codex.first_delta`, `voice.synthesis.end`, and `voice.playback.end`.

JSONL events include:

```json
{"event":"voice.synthesis.end","timestamp":"2026-05-28T00:00:00+00:00","session_id":"...","turn_id":"...","elapsed_ms":1234.567,"duration_ms":456.789}
```

`turn_id` is generated for each user turn. Events before a wake word is accepted may have `session_id: null`; once a session starts, subsequent events in the same turn carry the session ID. Span end events and matched `*.start` / `*.end` event pairs include `duration_ms`.

Summarize p50/p90/p95 by event:

```sh
uv run python scripts/latency_summary.py data/latency.jsonl
uv run python scripts/latency_summary.py data/latency.jsonl --metric duration_ms
```

Percentiles use linear interpolation between sorted samples.

## Codex Backend

AI responses are generated through `codex app-server --listen stdio://`.

- The app speaks JSON-RPC over stdio.
- It calls `initialize`, then `thread/start` or `thread/resume`, then `turn/start`.
- It reads `item/agentMessage/delta` and completed `agentMessage.text` events.
- If app-server fails, the error reason is shown as the AI response.
- `config/profile.json` keeps `assistant.model` as `null` by default so the user's Codex configuration chooses the model.
- Set `assistant.model` only if you need to force a specific Codex-supported model.

## Thread Policy

The app creates threads that are as close as possible to non-project Codex app chats.

`thread/start` and `thread/resume` explicitly pass:

```json
{
  "cwd": null,
  "runtimeWorkspaceRoots": [],
  "environments": []
}
```

The local SQLite database stores only the mapping from local `session_id` to Codex `thread_id`.

## Safety

The app-server thread uses conservative defaults:

```json
{
  "sandbox": "read-only",
  "approvalPolicy": "never",
  "ephemeral": false
}
```

Server-side requests are declined by default in this terminal MVP:

- command/file approval: decline
- MCP elicitation: decline
- permission request: empty permissions
- tool user input request: cancel

## Data

SQLite data is stored in:

```text
data/orbit_ai.sqlite3
```

Stored data includes:

- messages
- session summaries
- tasks
- memories
- proactive events
- Codex thread mappings

`data/*.sqlite3` is ignored by git.

## Development

```sh
make test
make lint
make format
make check
```

`make check` runs lint, pytest, compileall, and a CLI smoke test.

## Known Limitations

- `codex app-server` is experimental.
- Connector and skill availability depends on the user's Codex configuration and app-server support.
- Real AI turns may use network and account quota.
- First voice input may download a Whisper model.
- GUI, mobile apps, and direct email/calendar implementations are outside this MVP.
