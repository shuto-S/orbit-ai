# Demo

This transcript shows Orbit AI as a local-first secretary assistant with memory controls.

## Text-First Conversation

```text
$ ORBIT_AI_VOICE_INPUT=0 ORBIT_AI_VOICE_OUTPUT=0 uv run python -m app.main
Orbit AI Terminal

AI: こんにちは。何から始めますか？
User: 私の好みとして、回答は短めで実装寄りが好きです。あとでメール運用を確認したいです。
AI: 了解しました。メール運用の確認は、実装寄りに短く整理します。
User: ありがとう
AI: この件はいったんここまでにしますか？
User: うん
AI: わかりました。また呼んでください。
```

After the session ends, memory extraction can store durable facts:

```text
MEMORY #1 preference active 0.9 回答は短めで実装寄りが好きです
MEMORY #2 open_loop active 0.7 メール運用を確認したいです
```

## Memory Commands

```text
User: /remember メール確認を優先する
AI: Memory #1 saved.

User: /memory search メール
AI: Matching memories:
- #1 [manual] メール確認を優先する

User: /forget 1
AI: Memory #1 forgotten.
```

## Backend Options

Codex app-server remains the default:

```json
{
  "assistant": {
    "llm_backend": {
      "type": "app_server"
    }
  }
}
```

Ollama can run locally:

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
