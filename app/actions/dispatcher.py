from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.config.autonomy import AutonomyConfig
from app.config.permission_policy import PermissionDecision, PermissionPolicyConfig, evaluate_permission
from app.memory.store import MemoryStore


@dataclass(frozen=True)
class ActionRequest:
    action: str
    payload: dict[str, Any]
    request_id: str | None = None
    session_id: str | None = None
    actor: str = "internal"
    risk_level: str = "normal"
    user_explicit: bool = False
    source: str | None = None


@dataclass(frozen=True)
class ActionResult:
    action: str
    ok: bool
    message: str
    request_id: str | None = None
    session_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    error_type: str | None = None
    permission_decision: PermissionDecision | None = None


class ActionHandler(Protocol):
    def __call__(self, request: ActionRequest, store: MemoryStore) -> ActionResult: ...


PermissionHook = Callable[[ActionRequest], PermissionDecision]
ApprovalSink = Callable[[ActionRequest], int | None]


class ActionDispatcher:
    def __init__(
        self,
        store: MemoryStore,
        handlers: dict[str, ActionHandler] | None = None,
        permission_hook: PermissionHook | None = None,
        approval_sink: ApprovalSink | None = None,
    ) -> None:
        self.store = store
        self.handlers = handlers or {}
        self.permission_hook = permission_hook
        self.approval_sink = approval_sink

    def register(self, action: str, handler: ActionHandler) -> None:
        self.handlers[action] = handler

    def execute(self, request: ActionRequest) -> ActionResult:
        handler = self.handlers.get(request.action)
        if handler is None:
            return ActionResult(
                action=request.action,
                ok=False,
                message=f"Unknown action: {request.action}",
                request_id=request.request_id,
                session_id=request.session_id,
                error_type="unknown_action",
            )

        permission_decision = self.permission_hook(request) if self.permission_hook else None
        if permission_decision and permission_decision != PermissionDecision.ALLOW:
            if permission_decision == PermissionDecision.ASK and self.approval_sink is not None:
                approval_request_id = self.approval_sink(request)
                return ActionResult(
                    action=request.action,
                    ok=False,
                    message=_approval_message(approval_request_id),
                    request_id=request.request_id,
                    session_id=request.session_id,
                    data={"approval_request_id": approval_request_id},
                    error_type="approval_required",
                    permission_decision=permission_decision,
                )
            return ActionResult(
                action=request.action,
                ok=False,
                message=f"Action not executed: permission decision is {permission_decision.value}.",
                request_id=request.request_id,
                session_id=request.session_id,
                error_type="permission_not_allowed",
                permission_decision=permission_decision,
            )

        result = handler(request, self.store)
        return ActionResult(
            action=result.action,
            ok=result.ok,
            message=result.message,
            request_id=result.request_id,
            session_id=result.session_id,
            data=result.data,
            error_type=result.error_type,
            permission_decision=permission_decision,
        )


def create_permission_hook(
    autonomy: AutonomyConfig,
    policy: PermissionPolicyConfig | None = None,
) -> PermissionHook:
    return lambda request: evaluate_permission(
        request.action,
        autonomy,
        risk_level=request.risk_level,
        policy=policy,
        user_explicit=request.user_explicit,
    )


def create_store_approval_sink(store: MemoryStore) -> ApprovalSink:
    def sink(request: ActionRequest) -> int:
        return store.add_approval_request(
            action=request.action,
            payload=request.payload,
            reason=_approval_reason(request),
            risk_level=request.risk_level,
            source_session_id=request.session_id,
            metadata={
                "request_id": request.request_id,
                "actor": request.actor,
                "source": request.source,
                "user_explicit": request.user_explicit,
            },
        )

    return sink


def create_default_dispatcher(
    store: MemoryStore,
    permission_hook: PermissionHook | None = None,
    approval_sink: ApprovalSink | None = None,
    autonomy: AutonomyConfig | None = None,
    policy: PermissionPolicyConfig | None = None,
) -> ActionDispatcher:
    from app.actions.local_tasks import create_task, mark_task_done, snooze_task

    hook = permission_hook
    if hook is None and autonomy is not None:
        hook = create_permission_hook(autonomy, policy)

    return ActionDispatcher(
        store=store,
        permission_hook=hook,
        approval_sink=approval_sink,
        handlers={
            "create_task": create_task,
            "mark_task_done": mark_task_done,
            "snooze_task": snooze_task,
        },
    )


def _approval_message(approval_request_id: int | None) -> str:
    if approval_request_id is None:
        return "Action not executed: approval is required."
    return f"Action queued for approval: #{approval_request_id}."


def _approval_reason(request: ActionRequest) -> str:
    if request.source:
        return f"permission decision ask from {request.source}"
    return "permission decision ask"
