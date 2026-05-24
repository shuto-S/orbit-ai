import json
import select
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class CodexAppServerError(RuntimeError):
    pass


@dataclass(frozen=True)
class BackendResponse:
    text: str
    thread_id: str


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


class AppServerCodexBackend:
    def __init__(
        self,
        model: str | None = None,
        cwd: Path | None = None,
        rpc_client: JsonRpcClient | None = None,
    ) -> None:
        self.model = model
        self.cwd = cwd
        self.rpc_client = rpc_client or AppServerJsonRpcClient()

    def ask(self, prompt: str, thread_id: str | None = None, timeout: int = 120) -> BackendResponse:
        active_thread_id = self._resume_thread(thread_id, timeout) if thread_id else self._start_thread(timeout)
        text = self._start_turn(active_thread_id, prompt, timeout)
        if not text.strip():
            raise CodexAppServerError("Codex app-server returned an empty response")
        return BackendResponse(text=text.strip(), thread_id=active_thread_id)

    def build_thread_start_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "cwd": None,
            "runtimeWorkspaceRoots": [],
            "environments": [],
            "sandbox": "read-only",
            "approvalPolicy": "never",
            "ephemeral": False,
            "sessionStartSource": "startup",
            "threadSource": "user",
        }
        if self.model:
            params["model"] = self.model
        if self.cwd is not None:
            params["cwd"] = str(self.cwd)
        return params

    def build_turn_start_params(self, thread_id: str, prompt: str) -> dict[str, Any]:
        params: dict[str, Any] = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": prompt}],
        }
        if self.model:
            params["model"] = self.model
        return params

    def _start_thread(self, timeout: int) -> str:
        result = self.rpc_client.request("thread/start", self.build_thread_start_params(), timeout)
        thread_id = self._extract_thread_id(result)
        if not thread_id:
            raise CodexAppServerError("thread/start response did not include a thread id")
        return thread_id

    def _resume_thread(self, thread_id: str | None, timeout: int) -> str:
        if not thread_id:
            return self._start_thread(timeout)
        params: dict[str, Any] = {
            "threadId": thread_id,
            "cwd": None,
            "runtimeWorkspaceRoots": [],
            "environments": [],
            "sandbox": "read-only",
            "approvalPolicy": "never",
            "excludeTurns": True,
        }
        if self.model:
            params["model"] = self.model
        if self.cwd is not None:
            params["cwd"] = str(self.cwd)
        result = self.rpc_client.request("thread/resume", params, timeout)
        return self._extract_thread_id(result) or thread_id

    def _start_turn(self, thread_id: str, prompt: str, timeout: int) -> str:
        result = self.rpc_client.request("turn/start", self.build_turn_start_params(thread_id, prompt), timeout)
        turn_id = self._extract_turn_id(result)
        chunks: list[str] = []
        completed_item_text = ""
        while True:
            message = self.rpc_client.read_message(timeout)
            if self._is_server_request(message):
                self._decline_server_request(message)
                continue
            method = message.get("method")
            params = message.get("params") if isinstance(message.get("params"), dict) else {}
            if method == "item/agentMessage/delta" and self._matches_turn(params, thread_id, turn_id):
                chunks.append(str(params.get("delta", "")))
            if method == "item/completed" and self._matches_turn(params, thread_id, turn_id):
                completed_item_text = self._extract_completed_agent_text(params) or completed_item_text
            if method == "turn/completed" and self._matches_turn(params, thread_id, turn_id):
                self._raise_turn_error(params)
                return "".join(chunks) or completed_item_text
            if method == "thread/status/changed" and params.get("threadId") == thread_id:
                status = params.get("status")
                if isinstance(status, dict) and status.get("type") == "idle" and (chunks or completed_item_text):
                    return "".join(chunks) or completed_item_text
            if method == "thread/status/changed" and params.get("status") == "errored":
                raise CodexAppServerError("Codex turn errored")
            if method == "error" and self._matches_turn(params, thread_id, turn_id):
                raise CodexAppServerError(self._extract_error_message(params))

    def _decline_server_request(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        method = str(message.get("method", ""))
        if not isinstance(request_id, int):
            return
        if method in (
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        ):
            self.rpc_client.respond(request_id, {"decision": "decline"})
            return
        if method == "mcpServer/elicitation/request":
            self.rpc_client.respond(request_id, {"action": "decline", "content": None})
            return
        if method == "item/permissions/requestApproval":
            self.rpc_client.respond(request_id, {"permissions": {}, "scope": "turn"})
            return
        if method == "item/tool/requestUserInput":
            self.rpc_client.respond(request_id, {"action": "cancel"})
            return
        self.rpc_client.respond(request_id, {})

    @staticmethod
    def _extract_thread_id(result: dict[str, Any]) -> str | None:
        thread = result.get("thread")
        if isinstance(thread, dict):
            for key in ("id", "threadId"):
                value = thread.get(key)
                if isinstance(value, str):
                    return value
        value = result.get("threadId")
        return value if isinstance(value, str) else None

    @staticmethod
    def _extract_turn_id(result: dict[str, Any]) -> str | None:
        turn = result.get("turn")
        if isinstance(turn, dict):
            for key in ("id", "turnId"):
                value = turn.get(key)
                if isinstance(value, str):
                    return value
        value = result.get("turnId")
        return value if isinstance(value, str) else None

    @staticmethod
    def _matches_turn(params: dict[str, Any], thread_id: str, turn_id: str | None) -> bool:
        if params.get("threadId") != thread_id:
            return False
        return turn_id is None or params.get("turnId") in (None, turn_id)

    @staticmethod
    def _raise_turn_error(params: dict[str, Any]) -> None:
        turn = params.get("turn")
        if not isinstance(turn, dict) or turn.get("status") != "failed":
            return
        error = turn.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message:
                raise CodexAppServerError(message)
        raise CodexAppServerError("Codex turn failed")

    @staticmethod
    def _extract_error_message(params: dict[str, Any]) -> str:
        error = params.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message:
                return message
        return "Codex turn errored"

    @staticmethod
    def _is_server_request(message: dict[str, Any]) -> bool:
        return "id" in message and "method" in message and "result" not in message and "error" not in message

    @staticmethod
    def _extract_completed_agent_text(params: dict[str, Any]) -> str:
        item = params.get("item")
        if not isinstance(item, dict) or item.get("type") != "agentMessage":
            return ""
        text = item.get("text")
        return text if isinstance(text, str) else ""
