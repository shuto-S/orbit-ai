# Contributing to Orbit AI

Thanks for helping improve Orbit AI. This project aims to be a local-first, voice-capable personal AI assistant with transparent memory, permissioned autonomy, and deterministic tests.

## Development Setup

Requirements:

- Python 3.11+
- `uv`
- Docker, only when testing VOICEVOX speech output
- Codex CLI or Ollama for real LLM turns

Install and verify:

```sh
uv sync
make check
```

Run the terminal app without voice:

```sh
ORBIT_AI_VOICE_INPUT=0 ORBIT_AI_VOICE_OUTPUT=0 uv run python -m app.main
```

Run with VOICEVOX and speech recognition:

```sh
make run
```

## Contribution Workflow

1. Open an issue for non-trivial behavior changes.
2. Keep pull requests focused on one user-facing problem.
3. Match existing module boundaries and coding style.
4. Add or update tests for behavior changes.
5. Run `make check` before requesting review.

## Design Principles

- Keep the core assistant local-first and auditable.
- Do not add external dependencies unless they clearly reduce complexity.
- Prefer explicit permission checks over implicit autonomous actions.
- Keep memory writes explainable, source-backed, and user-controllable.
- Preserve existing public imports unless a migration path is documented.

## Testing Expectations

Use targeted tests while developing:

```sh
uv run pytest tests/test_memory.py
uv run pytest tests -k 'session or proactive or voice'
```

Before finalizing a PR:

```sh
make check
```

## Security and Privacy

Do not commit API keys, tokens, credentials, logs, generated databases, or personal data. If a change touches memory, logs, voice input, local commands, or external services, explain the privacy/security impact in the PR.

Report vulnerabilities privately using the process in `SECURITY.md`.
