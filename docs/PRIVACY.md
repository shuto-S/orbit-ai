# Privacy

Orbit AI is designed as a local-first assistant. Runtime data is stored inside the repository by default.

## Local Data

The app may write:

- `data/orbit_ai.sqlite3`: messages, summaries, memories, tasks, reviews, decision logs, and backend thread IDs.
- `data/latency.jsonl`: optional latency events when latency logging is enabled.
- `logs/orbit-ai.log`: daemon output when daemon mode is used.

These files are ignored by git.

## LLM Backends

Orbit AI supports multiple LLM backends:

- Codex app-server: prompts are sent to the configured Codex environment.
- Ollama: prompts are sent to a local Ollama daemon.

Review `config/profile.json` before running the app. Do not put secrets in prompts, logs, or memory.

## Memory Controls

Memory can be inspected and controlled from the CLI:

```text
/memory
/memory search <query>
/memory show <id>
/memory archive <id>
/remember <text>
/forget <id>
```

Sensitive-looking content such as passwords, API keys, tokens, and private keys is not saved automatically by the memory extractor.

## Deleting Local Data

Stop the app, then remove local runtime files:

```sh
rm -f data/orbit_ai.sqlite3 data/latency.jsonl logs/orbit-ai.log
```

Only delete files you intend to remove. Back up local data first when needed.
