from __future__ import annotations

import argparse
import copy
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CliOptions:
    llm_backend: str | None = None
    llm_model: str | None = None
    llm_base_url: str | None = None
    llm_timeout_seconds: int | None = None
    llm_options: dict[str, Any] | None = None


def parse_cli_options(argv: Sequence[str] | None = None) -> CliOptions:
    parser = argparse.ArgumentParser(prog="orbit-ai")
    parser.add_argument(
        "--llm-backend",
        choices=("app_server", "codex", "codex_app_server", "ollama"),
        help="LLM backend to use for this run. Overrides config/profile.json.",
    )
    parser.add_argument(
        "--llm-model",
        help="Model name for the selected LLM backend. Required for Ollama if config does not provide one.",
    )
    parser.add_argument(
        "--llm-base-url",
        help="Base URL for the selected LLM backend. Used by Ollama, for example http://127.0.0.1:11434.",
    )
    parser.add_argument(
        "--llm-timeout-seconds",
        type=_positive_int,
        help="Request timeout for the selected LLM backend.",
    )
    parser.add_argument(
        "--llm-option",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Backend option override. Repeatable. JSON values are accepted, for example num_ctx=8192.",
    )
    args = parser.parse_args(argv)
    try:
        llm_options = parse_llm_options(args.llm_option)
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))
    return CliOptions(
        llm_backend=args.llm_backend,
        llm_model=_optional_text(args.llm_model),
        llm_base_url=_optional_text(args.llm_base_url),
        llm_timeout_seconds=args.llm_timeout_seconds,
        llm_options=llm_options,
    )


def apply_cli_options(profile: dict[str, Any], options: CliOptions) -> dict[str, Any]:
    if not _has_llm_override(options):
        return profile

    updated = copy.deepcopy(profile)
    assistant = updated.get("assistant")
    if not isinstance(assistant, dict):
        assistant = {}
        updated["assistant"] = assistant

    current_backend = assistant.get("llm_backend")
    if not isinstance(current_backend, dict):
        current_backend = {}

    backend = dict(current_backend)
    if options.llm_backend:
        backend["type"] = _normalize_backend_type(options.llm_backend)
    if options.llm_model:
        backend["model"] = options.llm_model
    if options.llm_base_url:
        backend["base_url"] = options.llm_base_url
    if options.llm_timeout_seconds is not None:
        backend["timeout_seconds"] = options.llm_timeout_seconds
    if options.llm_options:
        merged_options = backend.get("options")
        if not isinstance(merged_options, dict):
            merged_options = {}
        backend["options"] = {**merged_options, **options.llm_options}

    assistant["llm_backend"] = backend
    return updated


def parse_llm_options(values: Sequence[str]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for value in values:
        key, separator, raw = value.partition("=")
        key = key.strip()
        if not key or not separator:
            raise argparse.ArgumentTypeError("--llm-option must be KEY=VALUE")
        parsed[key] = _parse_option_value(raw.strip())
    return parsed


def _has_llm_override(options: CliOptions) -> bool:
    return any(
        (
            options.llm_backend,
            options.llm_model,
            options.llm_base_url,
            options.llm_timeout_seconds is not None,
            options.llm_options,
        )
    )


def _normalize_backend_type(value: str) -> str:
    if value in ("codex", "codex_app_server"):
        return "app_server"
    return value


def _parse_option_value(value: str) -> Any:
    if not value:
        return ""
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return parsed
