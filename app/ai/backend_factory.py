from __future__ import annotations

from typing import Any

from app.ai.app_server_backend import AppServerCodexBackend
from app.ai.backends.base import LlmBackend, LlmBackendError
from app.ai.ollama_backend import OllamaBackend
from app.latency import LatencyLogger


def create_llm_backend(profile: dict[str, Any], latency: LatencyLogger | None = None) -> LlmBackend:
    assistant = profile.get("assistant", {})
    if not isinstance(assistant, dict):
        assistant = {}
    llm_backend = assistant.get("llm_backend")
    if not isinstance(llm_backend, dict):
        return create_app_server_backend(assistant, latency=latency)

    backend_type = str(llm_backend.get("type", "app_server")).strip().lower()
    if backend_type in ("app_server", "codex", "codex_app_server"):
        return create_app_server_backend(assistant, llm_backend, latency=latency)
    if backend_type == "ollama":
        model = str(llm_backend.get("model", "")).strip()
        if not model:
            raise LlmBackendError("assistant.llm_backend.model is required when type is 'ollama'")
        return OllamaBackend.from_config(llm_backend)
    raise LlmBackendError(f"unknown assistant.llm_backend.type: {backend_type}")


def describe_llm_backend(profile: dict[str, Any]) -> str:
    assistant = profile.get("assistant", {})
    if not isinstance(assistant, dict):
        assistant = {}
    llm_backend = assistant.get("llm_backend")
    if not isinstance(llm_backend, dict):
        return _format_app_server_description(assistant)

    backend_type = str(llm_backend.get("type", "app_server")).strip().lower()
    if backend_type in ("app_server", "codex", "codex_app_server"):
        return _format_app_server_description(assistant, llm_backend)
    if backend_type == "ollama":
        model = str(llm_backend.get("model") or "").strip() or "not configured"
        raw_base_url = llm_backend.get("base_url")
        base_url = raw_base_url.strip() if isinstance(raw_base_url, str) and raw_base_url.strip() else None
        return f"Ollama (model: {model}, base_url: {base_url or 'http://127.0.0.1:11434'})"
    return f"Unknown ({backend_type or 'not configured'})"


def create_app_server_backend(
    assistant: dict[str, Any],
    llm_backend: dict[str, Any] | None = None,
    latency: LatencyLogger | None = None,
) -> AppServerCodexBackend:
    model = ""
    if llm_backend:
        model = str(llm_backend.get("model") or "").strip()
    if not model:
        assistant_model = assistant.get("model")
        model = str(assistant_model).strip() if assistant_model else ""
    return AppServerCodexBackend(model=model or None, latency=latency)


def _format_app_server_description(
    assistant: dict[str, Any],
    llm_backend: dict[str, Any] | None = None,
) -> str:
    model = ""
    if llm_backend:
        model = str(llm_backend.get("model") or "").strip()
    if not model:
        assistant_model = assistant.get("model")
        model = str(assistant_model).strip() if assistant_model else ""
    return f"Codex app-server (model: {model or 'default'})"
