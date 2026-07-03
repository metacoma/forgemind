from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Protocol

from .registry import RUNTIME_ACTION_REGISTRY


class RuntimeAction(str, Enum):
    REPO_READ = "repo.read"
    FILE_READ = "file.read"
    FILE_WRITE = "file.write"
    SHELL_RUN = "shell.run"
    TEST_RUN = "test.run"
    GIT_READ = "git.read"
    GIT_COMMIT = "git.commit"
    GIT_PUSH = "git.push"
    PR_CREATE = "pr.create"
    INTERNET_SEARCH = "internet.search"
    K8S_READ = "k8s.read"
    K8S_APPLY = "k8s.apply"
    ARGOCD_SYNC = "argocd.sync"
    SSH_RUN = "ssh.run"
    UNKNOWN = "unknown"

    @classmethod
    def coerce(cls, value: object) -> "RuntimeAction":
        if isinstance(value, cls):
            return value
        canonical = RUNTIME_ACTION_REGISTRY.canonicalize(value)
        if canonical and canonical in {item.value for item in cls}:
            return cls(canonical)
        return cls.UNKNOWN


@dataclass(frozen=True)
class RuntimeSubject:
    kind: str
    name: str
    stage: str | None = None


@dataclass(frozen=True)
class RuntimeResource:
    kind: str
    name: str = "*"
    attributes: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ActionDecision:
    allowed: bool
    reason: str
    action: RuntimeAction
    subject: RuntimeSubject
    resource: RuntimeResource


class PolicyDecisionPoint(Protocol):
    def authorize(
        self,
        *,
        subject: RuntimeSubject,
        action: RuntimeAction,
        resource: RuntimeResource,
        context: Mapping[str, object] | None = None,
    ) -> ActionDecision:
        ...


class StaticStagePolicyDecisionPoint:
    """Small in-process action ACL used until OPA/Cedar action policy lands.

    This is intentionally explicit and fail-closed for known dangerous actions.
    Unknown actions are denied by default unless ``allow_unknown`` is enabled for
    a local development harness.
    """

    DEFAULT_STAGE_ALLOW: Mapping[str, frozenset[RuntimeAction]] = {
        stage: frozenset(RuntimeAction(item) for item in RUNTIME_ACTION_REGISTRY.stage_allowed(stage))
        for stage in RUNTIME_ACTION_REGISTRY.DEFAULT_STAGE_ALLOW
    }

    def __init__(self, *, stage_allow: Mapping[str, frozenset[RuntimeAction]] | None = None, allow_unknown: bool = False) -> None:
        self.stage_allow = dict(stage_allow or self.DEFAULT_STAGE_ALLOW)
        self.allow_unknown = allow_unknown

    def authorize(
        self,
        *,
        subject: RuntimeSubject,
        action: RuntimeAction,
        resource: RuntimeResource,
        context: Mapping[str, object] | None = None,
    ) -> ActionDecision:
        stage = str((context or {}).get("stage") or subject.stage or "").strip().lower()
        if action == RuntimeAction.UNKNOWN:
            return ActionDecision(self.allow_unknown, "unknown action allowed by dev override" if self.allow_unknown else "unknown action denied", action, subject, resource)
        allowed = action in self.stage_allow.get(stage, frozenset())
        return ActionDecision(allowed, f"action {action.value} {'allowed' if allowed else 'denied'} for stage {stage or '<unset>'}", action, subject, resource)


class PolicyEnforcementError(PermissionError):
    def __init__(self, decision: ActionDecision) -> None:
        self.decision = decision
        super().__init__(decision.reason)


class PolicyEnforcementPoint:
    def __init__(self, pdp: PolicyDecisionPoint | None = None) -> None:
        self.pdp = pdp or StaticStagePolicyDecisionPoint()

    def require(
        self,
        *,
        subject: RuntimeSubject,
        action: RuntimeAction | str,
        resource: RuntimeResource,
        context: Mapping[str, object] | None = None,
    ) -> ActionDecision:
        normalized_action = RuntimeAction.coerce(action)
        decision = self.pdp.authorize(subject=subject, action=normalized_action, resource=resource, context=context)
        if not decision.allowed:
            raise PolicyEnforcementError(decision)
        return decision

    def authorize_many(
        self,
        *,
        subject: RuntimeSubject,
        actions: list[RuntimeAction | str],
        resource: RuntimeResource,
        context: Mapping[str, object] | None = None,
    ) -> list[ActionDecision]:
        return [self.require(subject=subject, action=action, resource=resource, context=context) for action in actions]
