import json
import select
import shutil
import subprocess
import threading
from typing import Any, Protocol

from app.ai.backends.base import LlmBackendError


class CodexAppServerError(LlmBackendError):
    pass


class JsonRpcClient(Protocol):
    def request(self, method: str, params: dict[str, Any], timeout: int) -> dict[str, Any]: ...

    def read_message(self, timeout: int) -> dict[str, Any]: ...

    def respond(self, request_id: int, result: dict[str, Any]) -> None: ...


class AppServerJsonRpcClient:
    def __init__(self, command: list[str] | None = None) -> None:
        executable = shutil.which("codex")
        self.command = command or ([executable, "app-server", "--listen", "stdio://"] if executable else None)
        self._process: subprocess.Popen[str] | None = None
        self._next_id = 1
        self._lock = threading.Lock()
        self._initialized = False

    def request(self, method: str, params: dict[str, Any], timeout: int) -> dict[str, Any]:
        if method != "initialize":
            self._ensure_initialized(timeout)
        request_id = self._send(method, params)
        while True:
            message = self.read_message(timeout)
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise CodexAppServerError(f"{method} failed: {message['error']}")
            result = message.get("result")
            if not isinstance(result, dict):
                raise CodexAppServerError(f"{method} returned invalid result")
            return result

    def _ensure_initialized(self, timeout: int) -> None:
        if self._initialized:
            return
        result = self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "orbit-ai",
                    "title": "Orbit AI Terminal",
                    "version": "0.1.0",
                },
                "capabilities": {
                    "experimentalApi": True,
                },
            },
            timeout,
        )
        if "userAgent" not in result:
            raise CodexAppServerError("app-server initialize returned invalid result")
        self._initialized = True

    def read_message(self, _timeout: int) -> dict[str, Any]:
        process = self._ensure_process()
        if process.stdout is None:
            raise CodexAppServerError("app-server stdout is not available")
        ready, _, _ = select.select([process.stdout], [], [], _timeout)
        if not ready:
            raise CodexAppServerError("app-server response timed out")
        line = process.stdout.readline()
        if not line:
            raise CodexAppServerError("app-server closed stdout")
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CodexAppServerError(f"invalid app-server JSON: {line[:200]}") from exc
        if not isinstance(message, dict):
            raise CodexAppServerError("invalid app-server message")
        return message

    def respond(self, request_id: int, result: dict[str, Any]) -> None:
        process = self._ensure_process()
        if process.stdin is None:
            raise CodexAppServerError("app-server stdin is not available")
        payload = {"jsonrpc": "2.0", "id": request_id, "result": result}
        with self._lock:
            process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            process.stdin.flush()

    def _send(self, method: str, params: dict[str, Any]) -> int:
        process = self._ensure_process()
        if process.stdin is None:
            raise CodexAppServerError("app-server stdin is not available")
        with self._lock:
            request_id = self._next_id
            self._next_id += 1
            payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
            process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            process.stdin.flush()
        return request_id

    def _ensure_process(self) -> subprocess.Popen[str]:
        if self.command is None:
            raise CodexAppServerError("codex CLIが見つかりません。")
        if self._process is None or self._process.poll() is not None:
            try:
                self._process = subprocess.Popen(
                    self.command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                )
            except OSError as exc:
                raise CodexAppServerError(f"app-serverを起動できません: {exc}") from exc
        return self._process
