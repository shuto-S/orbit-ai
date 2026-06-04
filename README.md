# Orbit AI

[![Check](https://github.com/shuto-S/orbit-ai/actions/workflows/check.yml/badge.svg)](https://github.com/shuto-S/orbit-ai/actions/workflows/check.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Orbit AI is a local-first terminal and voice personal assistant for Japanese daily workflows. It combines inspectable memory, task follow-up, permissioned autonomy, and interchangeable LLM backends for Codex app-server and Ollama.

Project status: early MVP / pre-1.0. The core loop is usable, tested, and intentionally small; APIs and configuration may still change before a stable release.

## Why Orbit AI

- Local-first runtime data: messages, memories, tasks, reviews, and decision logs stay in SQLite files under `data/`.
- Inspectable memory: working, episodic, semantic, and prospective memory are exposed through commands and tests.
- Permissioned autonomy: proactive checks and internal actions go through explicit policy and audit logs.
- Backend choice: use Codex app-server by default or a local Ollama model when privacy or offline operation matters.
- Practical voice path: text is the primary input, with one-turn speech recognition available through `/voice` or `/v`.
- Reproducible verification: `make check` runs lint, tests, compile checks, and a CLI smoke test.

## Project Links

- [Setup guide](docs/SETUP.md)
- [Demo transcript](docs/DEMO.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Privacy notes](docs/PRIVACY.md)
- [Roadmap](ROADMAP.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [OpenAI OSS application notes](docs/OPENAI_OSS_APPLICATION.md)

Orbit starts a conversation on launch, continues naturally without requiring a wake word on every turn, and never ends a conversation without confirming with the user first.

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
- `/memory search <query>`: search active memories
- `/memory show <id>`: show memory metadata
- `/memory archive <id>`: hide an outdated memory without deleting it
- `/remember <text>`: manually save a memory
- `/forget <id>`: mark a memory as forgotten so it is not retrieved
- `/daily` or `/review`: show today's deterministic planning/review candidates and save the review
- `/tasks`: show open and snoozed tasks, including due information when present
- `/task done <id>`: mark a task as done
- `/task snooze <id> <when>`: snooze a task and save `<when>` as its due time
- `/loops`: show unresolved conversation topics that are not necessarily concrete tasks
- `/loop done <id>`: mark an open loop as resolved
- `/loop archive <id>`: archive an open loop
- `/approvals`: show pending local approval requests
- `/approve <id>`: mark an approval request as approved without executing it
- `/reject <id>`: mark an approval request as rejected
- `/drafts`: show saved local drafts
- `/draft show <id>`: show one draft body
- `/draft archive <id>`: archive a draft
- `/proactive`: check whether there is a proactive candidate
- `/reset`: discard the current session and return to idle

## Proactive Checks

Proactive behavior is configured in `config/proactive.json`.
Autonomy is configured in `config/autonomy.json`. The default is equivalent to `suggest_only`, so existing proactive checks can suggest a follow-up but still start with the permission prompt.
Permission decisions are configured in `config/permission_policy.json`. The policy helper evaluates an action name, the current autonomy config, and a risk level, then returns one of `allow`, `ask`, or `deny`.

Autonomy levels:

- `off`: no autonomous suggestions are made.
- `suggest_only`: Orbit may propose a follow-up candidate, but it does not execute actions.
- `assistive`: Orbit may allow low-risk local actions that the user explicitly requested in the current turn, while inferred or risky actions still ask.
- `ask_then_act`: future internal actions may run only after the permission prompt is accepted, and only when the action is explicitly allowed by config.

## Memory

Orbit stores memory in `data/orbit_ai.sqlite3`.

- Working memory: recent `messages` from the current session are included in each response prompt.
- Episodic memory: `session_summaries` records session summaries, decisions, open loops, and follow-up candidates.
- Semantic memory: `memories` stores durable user preferences, project context, decisions, and profile facts.
- Prospective memory: `tasks` stores open loops and snoozed follow-ups for daily/proactive checks.

Memory retrieval is local and dependency-free. `search_memories()` scores active memories by query match, priority, confidence, memory kind, and recency of use. Prompt injection is bounded by `memory.retrieval.max_prompt_chars` in `config/profile.json`.

On session close, Orbit tries LLM structured memory extraction when `memory.extraction.mode` is `llm`; invalid JSON or backend failures fall back to the deterministic keyword extractor. Sensitive-looking content such as passwords, API keys, tokens, and private keys is not saved automatically.

Memory records keep source metadata when available: source session, source message IDs, status, sensitivity, usage count, and timestamps. Existing SQLite databases are migrated in place with additive columns only.

`enabled=false` makes the effective level `off`. Unknown `level` values fall back to `suggest_only`. `allow_local_actions` defaults to `false`; this release does not add external service calls or automatic command execution.

Permission policy currently covers these internal action names:

- `create_task`
- `snooze_task`
- `mark_task_done`
- `write_memory`
- `run_local_check`

The default policy is `ask`, with explicit overrides in `rules`: `create_task` is `allow`, `run_local_check` is `deny`, and the other configured local actions are `ask`.
Unknown actions return `deny`. When autonomy is `off`, known actions also return `deny`. In `suggest_only`, executable actions are not auto-allowed and return `ask` unless the action policy explicitly denies them. In `assistive`, explicit low-risk `create_task` and `write_memory` requests can return `allow` when `allow_local_actions=true`; inferred actions, high-risk actions, unknown actions, and external actions do not auto-run. In `ask_then_act`, normal-risk actions can return `allow` only when the policy allows it, `allow_local_actions=true`, and the action is included in `autonomy.require_permission_for`; high-risk actions still return `ask`.

When the app is idle in text input mode, stdin is polled every `proactive.check_interval_seconds` equivalent (`check_interval_seconds` in the JSON file) so the policy can run without waiting forever inside `input()`.
If the policy allows an intervention, Orbit first asks the existing permission prompt and records the `proposed` event in `proactive_events`; accepting or rejecting the prompt records the existing `accepted` or `rejected` events.
When the policy does not allow an intervention, the app stays silent.

Tasks with `status=open` can become proactive candidates. Tasks with `status=snoozed` are excluded until their `due_at` value is due. `due_at` is parsed as ISO 8601 with the standard library, for example `2026-05-28T10:00:00+09:00` or `2026-05-28`; values that cannot be parsed, such as natural-language snooze text, are kept for display but treated as not due. Tasks with `status=done` or `status=cancelled` are never proactive candidates.

Each proactive evaluation also writes a pre-prompt audit entry to `decision_logs`.
This log is separate from `proactive_events`: it records why Orbit decided to ask permission or stay silent, while `proactive_events` records the user-facing prompt outcome after presentation.
Decision log rows include `kind`, nullable `session_id` / `task_id`, nullable `candidate_text`, `decision` such as `ask_permission` or `deny`, `reason`, nullable `score`, `metadata_json`, and `created_at`.
For proactive checks, `metadata_json` stores minimal state such as the trigger (`manual`, `idle`, or `direct`) and session state; it must not contain secrets or full conversation transcripts.

## Internal Actions

Typed internal actions live under `app/actions/`. `ActionRequest` carries the action name, payload, actor, session/request IDs, and risk level. `ActionResult` returns a stable audit-friendly shape with `ok`, `message`, `data`, `error_type`, and the permission decision that was applied.

`create_default_dispatcher(store, ...)` registers local task actions:

- `create_task`
- `snooze_task`
- `mark_task_done`

The dispatcher can receive either a `permission_hook` or `AutonomyConfig` plus optional `PermissionPolicyConfig`. Permission is evaluated before the handler runs. Unknown actions and invalid payloads return `ActionResult(ok=False, ...)` without raising, while unexpected storage/runtime errors still propagate to the caller.

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

On macOS, allow microphone access for Terminal or iTerm on first use. The default model is `base` to keep voice turns responsive. Use `small` or larger if you prefer accuracy over speed.

The default voice input settings keep immediate speech detection while biasing recognition toward Orbit-specific terms:

```json
{
  "backend": "command",
  "model": "base",
  "language": "ja",
  "max_seconds": 12,
  "min_seconds": 0.5,
  "silence_seconds": 0.8,
  "silence_threshold": 0.01,
  "noise_calibration_seconds": 0.0,
  "silence_threshold_multiplier": 2.5,
  "beam_size": 5,
  "best_of": 5,
  "temperature": 0.0,
  "initial_prompt": "Orbit AI assistant. Japanese conversation. Frequent words: オービット, オル, VOICEVOX, GitHub, issue, pull request, PR, Codex, タスク, 予定, メモ.",
  "hotwords": "オービット オル VOICEVOX GitHub issue pull request PR Codex タスク 予定 メモ"
}
```

Text input is the primary input path even when voice input is enabled. Type normal messages at `User:` and press Enter. To use speech recognition for a turn, type `/voice` or `/v` and press Enter; the app then starts one STT recording and uses the recognized text as the user turn. Japanese IME input stays on the normal terminal line editor, so conversion and Enter behavior should match regular shell input.

`noise_calibration_seconds` is off by default because command-based input already prints `Listening...`; if you start speaking during a hidden calibration window, the first words can be discarded. Set it to `0.5` or `0.7` only when room noise causes false starts. Increase `silence_seconds` if utterances are being cut off, and decrease it if turns feel too slow.

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

## LLM Backend

By default, AI responses are generated through `codex app-server --listen stdio://`.

`config/profile.json` can select a backend with `assistant.llm_backend`. If this key is omitted, Orbit keeps the existing Codex app-server behavior.

```json
{
  "assistant": {
    "llm_backend": {
      "type": "app_server"
    }
  }
}
```

You can also override the backend for one run without editing `config/profile.json`:

```sh
uv run python -m app.main --llm-backend app_server
uv run python -m app.main --llm-backend ollama --llm-model llama3.2:latest
```

When using `make run`, pass the same options through `ARGS`:

```sh
make run ARGS="--llm-backend ollama --llm-model llama3.2:latest"
```

Supported runtime options:

- `--llm-backend app_server|codex|codex_app_server|ollama`
- `--llm-model <model>`
- `--llm-base-url <url>` for Ollama, for example `http://127.0.0.1:11434`
- `--llm-timeout-seconds <seconds>`
- `--llm-option KEY=VALUE`, repeatable for backend options such as `num_ctx=8192`

### Codex App-Server

Codex app-server remains the default backend.

- The app speaks JSON-RPC over stdio.
- It calls `initialize`, then `thread/start` or `thread/resume`, then `turn/start`.
- It reads `item/agentMessage/delta` and completed `agentMessage.text` events.
- If app-server fails, the error reason is shown as the AI response.
- `config/profile.json` keeps `assistant.model` as `null` by default so the user's Codex configuration chooses the model.
- Set `assistant.model` only if you need to force a specific Codex-supported model.

### Ollama

Orbit can also use a local Ollama model through the native `/api/chat` endpoint. Start Ollama and pull the model first:

```sh
ollama serve
ollama pull llama3.2:latest
```

Then configure the assistant backend:

```json
{
  "assistant": {
    "llm_backend": {
      "type": "ollama",
      "base_url": "http://127.0.0.1:11434",
      "model": "llama3.2:latest",
      "timeout_seconds": 120,
      "stream": true,
      "options": {
        "temperature": 0.2,
        "num_ctx": 8192
      }
    }
  }
}
```

Useful local model candidates include `llama3.2:latest`, `qwen2.5:latest`, and `gemma3:latest`; choose based on local memory, speed, and Japanese quality. Ollama does not use Codex app-server thread state. Orbit still includes recent local messages in each prompt, so normal conversation context remains available without a DB schema change.

Troubleshooting:

- `Ollamaに接続できません`: check that `ollama serve` is running and `base_url` points to it.
- Missing model errors: run `ollama pull <model>`.
- Slow responses: use a smaller model or reduce `options.num_ctx`.

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
- daily reviews
- memories
- decision logs
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

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the subsystem map and data flow.

Internal structure:

- `app/main.py`: wires configuration, store, session manager, voice I/O, and the terminal loop.
- `app/cli/`: terminal display, command handlers, shutdown handling, and input loop helpers.
- `app/memory/`: `MemoryStore` facade plus domain repositories for the SQLite tables.
- `app/session/`: session state transitions, wake-word helpers, proactive policy, and close-session lifecycle.
- `app/io/`: voice configuration, input, playback, and the `VoiceIO` compatibility facade.
- `app/ai/`: prompt/response agents and Codex app-server RPC/streaming adapters.

## Known Limitations

- `codex app-server` is experimental.
- Connector and skill availability depends on the user's Codex configuration and app-server support.
- Real AI turns may use network and account quota.
- First voice input may download a Whisper model.
- GUI, mobile apps, and direct email/calendar implementations are outside this MVP.
