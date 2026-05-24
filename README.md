# Colleague AI Terminal

ターミナル上で動く「同僚型AIアシスタント」のMVPです。

ユーザーがAI名で呼びかけると会話を開始し、会話中はAI名なしで自然に続けられます。終了候補の発話を検出しても勝手には終了せず、必ず確認してから待機状態へ戻ります。

## セットアップ

Python 3.11以上と `uv` が必要です。

```sh
make run
```

VOICEVOXを止める場合:

```sh
make stop-voice
```

## 使い方

```text
User: オービット、相談したい
AI: ...

User: このアプリのMVPを整理したい
AI: ...

User: ありがとう
AI: この件はいったんここまでにしますか？

User: うん
AI: わかりました。また呼んでください。
```

## コマンド

- `/quit`: アプリ終了
- `/status`: 現在の状態とsession_idを表示
- `/memory`: 保存済みmemoryと直近summaryを表示
- `/proactive`: proactive候補があるか確認
- `/reset`: 現在セッションを破棄してIDLEへ戻す

## Voice I/O

簡易音声I/Oに対応しています。

音声出力はVOICEVOX Engineのみを通常起動対象にしています。`make run` はVOICEVOX Engineを起動してから、`COLLEAGUE_AI_VOICE_INPUT=1` と `COLLEAGUE_AI_VOICE_OUTPUT=1` でアプリを起動します。

VOICEVOX EngineはMakefileから制御できます。

```sh
make run
make status-voice
make logs-voice
make stop-voice
```

VOICEVOX操作は `scripts/voicevox.sh` に分離しています。

設定例:

```json
{
  "voice": {
    "output": {
      "enabled": true,
      "engine": "voicevox",
      "voicevox_url": "http://127.0.0.1:50021",
      "speaker": 3,
      "player": ["afplay"]
    }
  }
}
```

VOICEVOX EngineはHTTP APIとして `/audio_query` と `/synthesis` を使います。Engine未起動時はエラーを表示し、アプリ自体は継続します。

macOS標準の `say` を使いたい場合は、`engine` を `say` にしてください。

音声入力はfaster-whisperベースです。`voice.input.command` で `scripts/stt_faster_whisper.py` を呼び出します。

このscriptは `python-sounddevice` で直接マイク録音し、faster-whisperで文字起こしします。macOSでは初回実行時にTerminal/iTerm等へマイク権限を許可してください。既定モデルは会話の待ち時間を抑えるため `base` です。精度を優先する場合は `small` 以上に変更してください。

```json
{
  "voice": {
    "input": {
      "enabled": true,
      "command": [
        "uv",
        "run",
        "python",
        "scripts/stt_faster_whisper.py",
        "--model",
        "base",
        "--language",
        "ja",
        "--max-seconds",
        "12",
        "--silence-seconds",
        "1.0"
      ],
      "timeout_seconds": 120
    }
  }
}
```

既存音声ファイルを文字起こしする場合は `--audio-file path/to/audio.wav`、外部録音コマンドを使いたい場合は `--record-command ... {output}` も利用できます。

## Codex Backend

AI応答は `codex app-server --listen stdio://` をバックボーンとして生成します。`codex exec` や定型fallback応答には切り替えません。

- app-server protocolはJSON-RPC over stdioで扱います。
- `initialize` 後に `thread/start` / `thread/resume` / `turn/start` を使います。
- `item/agentMessage/delta` と `item/completed` の `agentMessage.text` から応答を取得します。
- app-serverが失敗した場合は、エラー理由をAI応答として表示します。
- 既定では `config/profile.json` の `assistant.model` を `null` にし、ユーザーの通常Codex設定の対応モデルを使います。
- ChatGPT認証ではプランや時期により指定可能モデルが変わるため、非対応モデルを固定しない方針です。
- モデルを固定したい場合だけ `assistant.model` にCodex対応モデル名を設定します。

## Thread Policy

Codex appの非プロジェクトチャットにできるだけ近いthreadとして作成します。

`thread/start` と `thread/resume` では以下を明示します。

```json
{
  "cwd": null,
  "runtimeWorkspaceRoots": [],
  "environments": []
}
```

ローカルSQLiteには `session_id -> codex_thread_id` の対応だけ保存し、同じローカル会話中は同じCodex threadを再利用します。

## Safety / Permissions

app-server threadは以下を基本設定にしています。

```json
{
  "sandbox": "read-only",
  "approvalPolicy": "never",
  "ephemeral": false
}
```

server requestが来た場合は、ターミナルMVPでは原則として安全側に倒します。

- command/file approval: decline
- MCP elicitation: decline
- permission request: 空permissions
- tool user input request: cancel

## Data

SQLite DBは `data/colleague_ai.sqlite3` に保存されます。

主な保存対象:

- messages
- session summaries
- memories
- proactive events
- codex thread mapping

`data/*.sqlite3` は `.gitignore` 対象です。

## Development

```sh
make test
make lint
make format
make check
```

`make check` は lint、pytest、compileall、CLI smokeを実行します。

## Known Limitations

- app-serverはCodex CLI上でexperimental扱いです。
- 外部連携やskillsが使えるかは、ユーザーのCodex設定とapp-server側対応に依存します。
- 実AI turnはコストとネットワークを使います。
- 音声入力はローカルマイク録音とfaster-whisperで処理します。初回はWhisperモデルの取得が必要です。
- GUI、メールAPIの直接実装はMVP範囲外です。
