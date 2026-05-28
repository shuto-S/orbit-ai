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


class ActionDispatcher:
    def __init__(
        self,
        store: MemoryStore,
        handlers: dict[str, ActionHandler] | None = None,
        permission_hook: PermissionHook | None = None,
    ) -> None:
        self.store = store
        self.handlers = handlers or {}
        self.permission_hook = permission_hook

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
    )


def create_default_dispatcher(
    store: MemoryStore,
    permission_hook: PermissionHook | None = None,
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
        handlers={
            "create_task": create_task,
            "mark_task_done": mark_task_done,
            "snooze_task": snooze_task,
        },
    )
