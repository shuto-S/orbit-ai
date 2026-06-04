UV_CACHE_DIR ?= .uv-cache
UV := UV_CACHE_DIR=$(UV_CACHE_DIR) uv
VOICEVOX := ./scripts/voicevox.sh

.PHONY: help run run-daemon stop-voice status-voice logs-voice test lint format compile check smoke

help:
	@printf '%s\n' \
		'Targets:' \
		'  make run          Start VOICEVOX and run the app with voice input/output' \
		'  make run-daemon   Run the app in a restart loop and append logs to logs/orbit-ai.log' \
		'  make stop-voice   Stop the VOICEVOX container' \
		'  make status-voice Show VOICEVOX container/API status' \
		'  make logs-voice   Follow VOICEVOX logs' \
		'  make check        Run lint, tests, compile, and smoke'

run:
	$(VOICEVOX) up
	ORBIT_AI_VOICE_INPUT=1 ORBIT_AI_VOICE_OUTPUT=1 $(UV) run python -m app.main $(ARGS)

run-daemon:
	./scripts/boot.sh

stop-voice:
	$(VOICEVOX) down

status-voice:
	$(VOICEVOX) status

logs-voice:
	$(VOICEVOX) logs

test:
	$(UV) run pytest

lint:
	$(UV) run ruff check .

format:
	$(UV) run ruff format .

compile:
	$(UV) run python -m compileall app tests

smoke:
	printf '/status\n/quit\n' | ORBIT_AI_VOICE_INPUT=0 ORBIT_AI_VOICE_OUTPUT=0 $(UV) run python -m app.main

check: lint test compile smoke
