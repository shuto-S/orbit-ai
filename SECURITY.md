# Security Policy

## Supported Versions

Orbit AI is pre-1.0. Security fixes are applied to the `main` branch. Tagged releases may be patched when they are still practical to support.

## Reporting a Vulnerability

Please do not open a public issue for a vulnerability.

Report privately through GitHub private vulnerability reporting if enabled, or contact the maintainer through the repository owner profile.

Include:

- A concise description of the issue.
- Affected files, commands, or configuration.
- Reproduction steps using dummy data.
- The impact and any known workaround.

Do not include real API keys, tokens, credentials, private transcripts, or personal data.

## Security Scope

Important areas include:

- Memory persistence in `data/orbit_ai.sqlite3`.
- Voice transcripts and daemon logs.
- LLM backend configuration for Codex app-server and Ollama.
- Permission policies for local actions.
- Proactive checks and decision logs.

## Privacy Notes

Orbit AI is designed to keep runtime data local by default. However, real LLM backends may send prompts to their configured provider. Review `config/profile.json` before running the app, and avoid storing secrets in memory.

Generated SQLite databases and log files are ignored by git. Rotate or delete local logs when they may contain private conversation text.
