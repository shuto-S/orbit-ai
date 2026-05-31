# OpenAI OSS Application Notes

This document summarizes why Orbit AI is a good candidate for OpenAI open-source support programs.

Official application entry points:

- Codex for Open Source: https://openai.com/form/codex-for-oss/
- Codex open source fund: https://openai.com/form/codex-open-source-fund/

As of 2026-05-31, OpenAI's public forms emphasize active maintainers, meaningful usage or ecosystem importance, evidence of maintenance work, public GitHub visibility, and specific API-credit usage for coding, review, automation, releases, or core OSS work.

## Short Pitch

Orbit AI is a local-first personal AI assistant framework for terminal and voice workflows. It combines text-first interaction, optional Japanese speech I/O, explicit conversation lifecycle management, persistent memory, task follow-up, permissioned autonomy, and interchangeable LLM backends for Codex app-server and Ollama. The project focuses on making personal agents inspectable, testable, and privacy-aware rather than opaque background automation.

## Why It Matters

Personal AI agents need safer defaults:

- Local storage that users can inspect and delete.
- Explicit permission checks before autonomous actions.
- Reproducible tests for memory, task, proactive, voice, and backend behavior.
- Support for both hosted and local models.
- Clear audit logs for proactive decisions.

Orbit AI is built around these constraints and can serve as a compact reference implementation for local-first agent behavior.

## Current Capabilities

- Terminal assistant loop with startup greeting and explicit end confirmation.
- Text-first input with optional one-turn voice recognition.
- VOICEVOX speech output support.
- Codex app-server backend.
- Ollama backend using the native `/api/chat` endpoint.
- SQLite memory with working, episodic, semantic, and prospective layers.
- Structured memory extraction with fallback behavior.
- User-controlled `/remember`, `/forget`, and `/memory search`.
- Task, daily review, proactive checks, permission policy, and internal action dispatcher.
- Deterministic tests and `make check`.

## How OpenAI Credits Would Help

API credits would be used for:

- Evaluating structured memory extraction quality across scenario fixtures.
- Generating and replaying agent safety scenarios.
- Testing Codex app-server behavior on pull request review, issue triage, and local task workflows.
- Comparing hosted models with local Ollama models on Japanese assistant conversations.
- Building regression tests for permissioned autonomy and proactive suggestions.

## Evidence to Maintain

Before applying, keep these items current:

- Public repository with license and contribution guide.
- Passing GitHub Actions checks.
- Clear README setup and demo.
- Tagged release with changelog.
- Issue labels and templates.
- Documented privacy and security policy.
- Public project roadmap.
- Real examples or short demo video.

## Candidate Application Copy

These drafts are intentionally short because the public form fields have character limits.

Qualification:

> Orbit AI is an active local-first personal agent project focused on inspectable memory, permissioned autonomy, and Codex/Ollama backends for Japanese terminal and voice workflows. It is important as a compact reference for privacy-aware personal agents with deterministic tests and auditable local state.

API credit usage:

> API credits would support regression evaluation for structured memory extraction, proactive safety scenarios, PR/issue maintenance automation, and comparison of hosted Codex behavior with local Ollama models on Japanese assistant workflows.

Additional context:

> The project keeps runtime data local, documents privacy/security behavior, and uses `make check` to verify memory, tasks, proactive policy, voice, latency, and backend behavior.

## Remaining Public Signals To Improve

- Publish the repository if it is still private.
- Add a first tagged release after the current documentation set is merged.
- Enable GitHub Discussions if support questions should not become issues.
- Use the standard labels consistently: `bug`, `enhancement`, `question`, `security`, `memory`, `voice`, `backend`, `autonomy`, `documentation`, `ci`, `dependencies`, and `good first issue`.
- Add a short demo video or terminal recording to the README.
- Keep issues understandable to external contributors, with expected behavior, acceptance criteria, and verification commands.
- Add examples of real maintenance work: reviewed PRs, issue triage, releases, and security fixes.

## Application Checklist

- [x] Repository is public.
- [x] `LICENSE` exists.
- [x] `README.md` explains the project in the first screen.
- [x] `CONTRIBUTING.md`, `SECURITY.md`, and `CODE_OF_CONDUCT.md` exist.
- [x] GitHub Actions runs `make check`.
- [ ] At least one tagged release exists.
- [x] Repository description and topics are set.
- [x] Demo transcript is linked.
- [x] Standard issue labels exist; future open issues should keep using them.
- [x] The credit use case is specific and tied to maintenance.
