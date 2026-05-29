from collections.abc import Iterator
from typing import Any

from app.ai.app_server_rpc import CodexAppServerError, JsonRpcClient
from app.ai.backends.base import BackendStreamEvent
from app.latency import DISABLED_LATENCY_LOGGER, LatencyLogger

CodexStreamEvent = BackendStreamEvent


class CodexTurnStreamer:
    def __init__(self, rpc_client: JsonRpcClient, latency: LatencyLogger | None = None) -> None:
        self.rpc_client = rpc_client
        self.latency = latency or DISABLED_LATENCY_LOGGER

    def stream(self, thread_id: str, turn_params: dict[str, Any], timeout: int) -> Iterator[CodexStreamEvent]:
        self.latency.event("codex.turn.start")
        result = self.rpc_client.request("turn/start", turn_params, timeout)
        turn_id = extract_turn_id(result)
        chunks: list[str] = []
        completed_item_text = ""
        first_delta_seen = False
        while True:
            message = self.rpc_client.read_message(timeout)
            if is_server_request(message):
                decline_server_request(self.rpc_client, message)
                continue
            method = message.get("method")
            params = message.get("params") if isinstance(message.get("params"), dict) else {}
            if method == "item/agentMessage/delta" and matches_turn(params, thread_id, turn_id):
                delta = str(params.get("delta", ""))
                if delta:
                    if not first_delta_seen:
                        self.latency.event("codex.first_delta")
                        first_delta_seen = True
                    chunks.append(delta)
                    yield CodexStreamEvent("delta", delta, thread_id)
            if method == "item/completed" and matches_turn(params, thread_id, turn_id):
                completed_item_text = extract_completed_agent_text(params) or completed_item_text
            if method == "turn/completed" and matches_turn(params, thread_id, turn_id):
                raise_turn_error(params)
                self.latency.event("codex.turn.end")
                yield CodexStreamEvent("completed", "".join(chunks) or completed_item_text, thread_id)
                return
            if method == "thread/status/changed" and params.get("threadId") == thread_id:
                status = params.get("status")
                if isinstance(status, dict) and status.get("type") == "idle" and (chunks or completed_item_text):
                    self.latency.event("codex.turn.end")
                    yield CodexStreamEvent("completed", "".join(chunks) or completed_item_text, thread_id)
                    return
            if method == "thread/status/changed" and params.get("status") == "errored":
                raise CodexAppServerError("Codex turn errored")
            if method == "error" and matches_turn(params, thread_id, turn_id):
                raise CodexAppServerError(extract_error_message(params))


def decline_server_request(rpc_client: JsonRpcClient, message: dict[str, Any]) -> None:
    request_id = message.get("id")
    method = str(message.get("method", ""))
    if not isinstance(request_id, int):
        return
    if method in (
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
    ):
        rpc_client.respond(request_id, {"decision": "decline"})
        return
    if method == "mcpServer/elicitation/request":
        rpc_client.respond(request_id, {"action": "decline", "content": None})
        return
    if method == "item/permissions/requestApproval":
        rpc_client.respond(request_id, {"permissions": {}, "scope": "turn"})
        return
    if method == "item/tool/requestUserInput":
        rpc_client.respond(request_id, {"action": "cancel"})
        return
    rpc_client.respond(request_id, {})


def extract_thread_id(result: dict[str, Any]) -> str | None:
    thread = result.get("thread")
    if isinstance(thread, dict):
        for key in ("id", "threadId"):
            value = thread.get(key)
            if isinstance(value, str):
                return value
    value = result.get("threadId")
    return value if isinstance(value, str) else None


def extract_turn_id(result: dict[str, Any]) -> str | None:
    turn = result.get("turn")
    if isinstance(turn, dict):
        for key in ("id", "turnId"):
            value = turn.get(key)
            if isinstance(value, str):
                return value
    value = result.get("turnId")
    return value if isinstance(value, str) else None


def matches_turn(params: dict[str, Any], thread_id: str, turn_id: str | None) -> bool:
    if params.get("threadId") != thread_id:
        return False
    return turn_id is None or params.get("turnId") in (None, turn_id)


def raise_turn_error(params: dict[str, Any]) -> None:
    turn = params.get("turn")
    if not isinstance(turn, dict) or turn.get("status") != "failed":
        return
    error = turn.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message:
            raise CodexAppServerError(message)
    raise CodexAppServerError("Codex turn failed")


def extract_error_message(params: dict[str, Any]) -> str:
    error = params.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message:
            return message
    return "Codex turn errored"


def is_server_request(message: dict[str, Any]) -> bool:
    return "id" in message and "method" in message and "result" not in message and "error" not in message


def extract_completed_agent_text(params: dict[str, Any]) -> str:
    item = params.get("item")
    if not isinstance(item, dict) or item.get("type") != "agentMessage":
        return ""
    text = item.get("text")
    return text if isinstance(text, str) else ""
