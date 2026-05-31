# Setup Guide

## Base Setup

Install requirements:

- Python 3.11+
- `uv`
- Git

Clone and verify:

```sh
git clone https://github.com/shuto-S/orbit-ai.git
cd orbit-ai
uv sync
make check
```

Run without voice:

```sh
ORBIT_AI_VOICE_INPUT=0 ORBIT_AI_VOICE_OUTPUT=0 uv run python -m app.main
```

## Codex App-Server

Codex app-server is the default backend. Ensure the Codex CLI is installed and configured, then use:

```json
{
  "assistant": {
    "llm_backend": {
      "type": "app_server"
    }
  }
}
```

## Ollama

Install Ollama, then pull a local model:

```sh
ollama pull qwen3-vl:4b
```

Configure:

```json
{
  "assistant": {
    "llm_backend": {
      "type": "ollama",
      "base_url": "http://127.0.0.1:11434",
      "model": "qwen3-vl:4b",
      "stream": true
    }
  }
}
```

## Voice Output

VOICEVOX can be run through Docker:

```sh
make run
make stop-voice
```

## Troubleshooting

- If `make check` fails during dependency resolution, run `uv sync` and retry.
- If Ollama fails, confirm `ollama list` works and the configured model exists.
- If voice input fails on macOS, allow microphone access for your terminal application.
- If generated logs contain private text, delete `logs/orbit-ai.log`.
