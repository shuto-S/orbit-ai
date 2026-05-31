from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Iterator
from typing import Any, Protocol

from app.ai.backends.base import BackendResponse, BackendStreamEvent, LlmBackendError


class OllamaResponse(Protocol):
    def __iter__(self) -> Iterator[bytes]: ...

    def read(self) -> bytes: ...

    def close(self) -> None: ...


UrlOpen = Callable[..., OllamaResponse]
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"


class OllamaBackend:
    def __init__(
        self,
        model: str,
        base_url: str = DEFAULT_OLLAMA_BASE_URL,
        options: dict[str, Any] | None = None,
        stream: bool = True,
        timeout_seconds: int = 120,
        urlopen: UrlOpen | None = None,
    ) -> None:
        self.model = model.strip()
        self.base_url = base_url.rstrip("/")
        self.options = options or {}
        self.stream = stream
        self.timeout_seconds = timeout_seconds
        self.urlopen = urlopen or urllib.request.urlopen
        self.thread_id = f"ollama:{uuid.uuid4()}"

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> OllamaBackend:
        model = str(config.get("model", "")).strip()
        base_url = str(config.get("base_url") or DEFAULT_OLLAMA_BASE_URL)
        options = config.get("options", {})
        if not isinstance(options, dict):
            options = {}
        return cls(
            model=model,
            base_url=base_url,
            options=options,
            stream=bool(config.get("stream", True)),
            timeout_seconds=int(config.get("timeout_seconds", 120)),
        )

    def ask(self, prompt: str, thread_id: str | None = None, timeout: int | None = None) -> BackendResponse:
        effective_timeout = self._timeout(timeout)
        if self.stream:
            chunks: list[str] = []
            completed_thread_id = thread_id
            for event in self.ask_stream(prompt, thread_id=thread_id, timeout=effective_timeout):
                completed_thread_id = event.thread_id
                if event.kind == "delta":
                    chunks.append(event.text)
                elif event.kind == "completed" and not chunks:
                    chunks.append(event.text)
            text = "".join(chunks).strip()
            if not text:
                raise LlmBackendError("Ollama returned an empty response")
            return BackendResponse(text=text, thread_id=completed_thread_id or self.thread_id)

        active_thread_id = thread_id or self.thread_id
        response = self._open_chat(prompt, timeout=effective_timeout, stream=False)
        try:
            payload = self._decode_json(response.read())
        finally:
            response.close()
        text = self._extract_content(payload).strip()
        if not text:
            raise LlmBackendError("Ollama returned an empty response")
        return BackendResponse(text=text, thread_id=active_thread_id)

    def ask_stream(
        self,
        prompt: str,
        thread_id: str | None = None,
        timeout: int | None = None,
    ) -> Iterator[BackendStreamEvent]:
        active_thread_id = thread_id or self.thread_id
        response = self._open_chat(prompt, timeout=self._timeout(timeout), stream=True)
        chunks: list[str] = []
        saw_done = False
        try:
            try:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    payload = self._decode_json(line)
                    error = payload.get("error")
                    if isinstance(error, str) and error:
                        raise LlmBackendError(self._format_api_error(error))
                    content = self._extract_content(payload)
                    if content:
                        chunks.append(content)
                        yield BackendStreamEvent("delta", content, active_thread_id)
                    if payload.get("done") is True:
                        saw_done = True
                        yield BackendStreamEvent("completed", "".join(chunks), active_thread_id)
                        return
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise LlmBackendError("Ollama stream was interrupted before completion") from exc
        finally:
            response.close()
        if not saw_done:
            if chunks:
                raise LlmBackendError("Ollama stream ended before done=true")
            raise LlmBackendError("Ollama returned an empty response")

    def _open_chat(self, prompt: str, timeout: int, stream: bool) -> OllamaResponse:
        self._ensure_model()
        try:
            request = urllib.request.Request(
                self._chat_url(),
                data=json.dumps(self._build_payload(prompt, stream=stream), ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            return self.urlopen(request, timeout=timeout)
        except ValueError as exc:
            raise LlmBackendError(self._format_base_url_error()) from exc
        except urllib.error.HTTPError as exc:
            raise LlmBackendError(self._format_http_error(exc)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LlmBackendError(
                "Ollamaに接続できません。ollama serve が起動しているか確認してください。"
            ) from exc

    def _chat_url(self) -> str:
        parsed = urllib.parse.urlparse(self.base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise LlmBackendError(self._format_base_url_error())
        return f"{self.base_url}/api/chat"

    def _timeout(self, timeout: int | None) -> int:
        return timeout if timeout is not None else self.timeout_seconds

    def _build_payload(self, prompt: str, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": stream,
        }
        if self.options:
            payload["options"] = self.options
        return payload

    def _ensure_model(self) -> None:
        if not self.model:
            raise LlmBackendError("Ollama model is not configured")

    @staticmethod
    def _decode_json(data: bytes | str) -> dict[str, Any]:
        if isinstance(data, bytes):
            data = data.decode("utf-8", errors="replace")
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise LlmBackendError("Ollama returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise LlmBackendError("Ollama returned invalid JSON")
        return payload

    @staticmethod
    def _extract_content(payload: dict[str, Any]) -> str:
        message = payload.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content
        response = payload.get("response")
        return response if isinstance(response, str) else ""

    def _format_http_error(self, exc: urllib.error.HTTPError) -> str:
        detail = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
            payload = self._decode_json(body)
            error = payload.get("error")
            if isinstance(error, str):
                detail = error
        except LlmBackendError:
            detail = ""
        suffix = f": {detail}" if detail else ""
        suggestion = f" `ollama pull {self.model}` を実行してモデルを取得してください。" if self.model else ""
        return f"Ollama API error HTTP {exc.code}{suffix}.{suggestion}".strip()

    def _format_api_error(self, error: str) -> str:
        if "model" in error.lower() and self.model:
            return f"{error}。`ollama pull {self.model}` を実行してモデルを取得してください。"
        return error

    @staticmethod
    def _format_base_url_error() -> str:
        return (
            "assistant.llm_backend.base_url must include http:// or https://, "
            f"for example {DEFAULT_OLLAMA_BASE_URL}"
        )
