from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal, Protocol


class LlmBackendError(RuntimeError):
    pass


@dataclass(frozen=True)
class BackendResponse:
    text: str
    thread_id: str


@dataclass(frozen=True)
class BackendStreamEvent:
    kind: Literal["progress", "delta", "completed"]
    text: str
    thread_id: str


class LlmBackend(Protocol):
    def ask(self, prompt: str, thread_id: str | None = None, timeout: int | None = None) -> BackendResponse: ...

    def ask_stream(
        self,
        prompt: str,
        thread_id: str | None = None,
        timeout: int | None = None,
    ) -> Iterator[BackendStreamEvent]: ...
