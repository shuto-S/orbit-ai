# Architecture

Orbit AI is a small local-first assistant. The main design goal is to keep each subsystem inspectable, testable, and replaceable without changing user-facing commands.

## Runtime Flow

1. `app/main.py` loads JSON configuration from `config/`.
2. `MemoryStore` opens the local SQLite database under `data/`.
3. `SessionManager` starts the launch greeting and owns the conversation state machine.
4. `PetUI` starts an optional desktop overlay for assistant speech, progress, autonomous notifications, and pet-submitted prompts.
5. `app/cli/runtime.py` reads terminal or pet-submitted text input, handles slash commands, and optionally triggers one-turn voice input.
6. Normal user turns are converted into a response prompt by `PromptBuilder`.
7. `ResponseAgent` sends the prompt to the configured LLM backend.
8. The assistant response and local memory artifacts are persisted through `MemoryStore`.

## Subsystems

- `app/cli/`: terminal loop, slash commands, display formatting, shutdown handling, and idle proactive ticks.
- `app/session/`: wake handling, session state transitions, end confirmation, proactive policy, and close-session lifecycle.
- `app/ai/`: backend factory, Codex app-server JSON-RPC, Ollama `/api/chat`, response prompting, and error shaping.
- `app/memory/`: SQLite facade, repositories, models, retrieval, summarization, and memory extraction.
- `app/actions/`: typed local action requests, permission evaluation, and task action handlers.
- `app/io/`: voice configuration, STT input, VOICEVOX playback, and the `VoiceIO` compatibility facade.
- `app/ui/`: optional desktop pet overlay and the JSONL client used by the CLI runtime. This is an additive display/input surface; terminal and voice output remain authoritative, and native overlay build/start failures fall back to terminal output.
- `scripts/`: operational helpers for VOICEVOX, latency summaries, daemon boot, and speech transcription.
- `tests/`: subsystem tests plus helper fakes for deterministic backend and session behavior.

## Data Boundaries

Local runtime data is stored under `data/` and ignored by git:

- `orbit_ai.sqlite3`: messages, summaries, memories, tasks, reviews, decision logs, proactive events, and Codex thread mappings.
- `latency.jsonl`: optional timing events when latency logging is enabled.

Logs are stored under `logs/` and ignored by git. Logs may contain conversation text, so they should not be shared without review.

## LLM Backends

The backend interface is intentionally narrow:

- `ask(prompt, thread_id, timeout)` returns final text and an optional backend thread ID.
- `ask_stream(prompt, thread_id, timeout)` yields streaming events when supported.

Codex app-server is the default backend. Ollama is available for local model use. Orbit still stores recent local messages and retrieved memories itself, so memory behavior does not depend on the backend provider.

## Memory Model

Orbit uses four practical memory layers:

- Working memory: recent messages in the current session.
- Episodic memory: session summaries, decisions, open loops, and follow-up candidates.
- Semantic memory: durable user preferences, facts, project context, and decisions.
- Prospective memory: open tasks, snoozes, and proactive follow-up candidates.

Memory extraction uses an LLM when configured, then falls back to a deterministic extractor if the backend fails or returns invalid JSON. Sensitive-looking values are skipped by the extractor.

## Permissioned Autonomy

Autonomous behavior is split into proposal, policy, and action:

- `ProactivePolicy` decides whether a candidate should be surfaced.
- `decision_logs` records why Orbit asked or stayed silent.
- `proactive_events` records the user-facing prompt outcome.
- `ActionDispatcher` evaluates permission before running local task actions.

The default configuration is conservative: suggestions are allowed, unknown actions are denied, and local actions require explicit policy.

## Compatibility Rules

The project preserves public imports used by tests and downstream scripts:

- `from app.memory.store import MemoryStore`
- `from app.io.voice import VoiceIO, VoiceConfig`
- `from app.ai.app_server_backend import AppServerCodexBackend`

Refactors should keep these facades intact unless a documented migration path exists.
