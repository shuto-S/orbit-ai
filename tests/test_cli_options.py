from __future__ import annotations

import pytest

from app.cli.options import apply_cli_options, parse_cli_options


def test_cli_options_override_profile_with_ollama_backend() -> None:
    profile = {
        "assistant": {
            "model": None,
            "llm_backend": {"type": "app_server"},
        }
    }
    options = parse_cli_options(
        [
            "--llm-backend",
            "ollama",
            "--llm-model",
            "qwen2.5:latest",
            "--llm-base-url",
            "http://127.0.0.1:11434",
            "--llm-timeout-seconds",
            "45",
            "--llm-option",
            "temperature=0.2",
            "--llm-option",
            "num_ctx=8192",
        ]
    )

    updated = apply_cli_options(profile, options)

    assert profile["assistant"]["llm_backend"] == {"type": "app_server"}
    assert updated["assistant"]["llm_backend"] == {
        "type": "ollama",
        "model": "qwen2.5:latest",
        "base_url": "http://127.0.0.1:11434",
        "timeout_seconds": 45,
        "options": {"temperature": 0.2, "num_ctx": 8192},
    }


def test_cli_options_can_select_codex_backend_alias() -> None:
    options = parse_cli_options(["--llm-backend", "codex", "--llm-model", "gpt-5-nano"])

    updated = apply_cli_options({"assistant": {}}, options)

    assert updated["assistant"]["llm_backend"] == {"type": "app_server", "model": "gpt-5-nano"}


def test_cli_options_without_overrides_reuses_profile_object() -> None:
    profile = {"assistant": {"llm_backend": {"type": "app_server"}}}

    assert apply_cli_options(profile, parse_cli_options([])) is profile


def test_cli_options_reject_invalid_timeout() -> None:
    with pytest.raises(SystemExit):
        parse_cli_options(["--llm-timeout-seconds", "0"])


def test_cli_options_reject_invalid_backend_option() -> None:
    with pytest.raises(SystemExit):
        parse_cli_options(["--llm-option", "temperature"])
