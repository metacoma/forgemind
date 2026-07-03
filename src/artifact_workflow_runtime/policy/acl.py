from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Protocol


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
        text = normalize_runtime_action_token(value)
        if not text:
            return cls.UNKNOWN
        if text in {item.value for item in cls}:
            return cls(text)
        return RUNTIME_ACTION_ALIASES.get(text, cls.UNKNOWN)


RUNTIME_ACTION_ALIASES: Mapping[str, RuntimeAction] = MappingProxyType({
    # repo / files
    "read_repo": RuntimeAction.REPO_READ,
    "repo_read": RuntimeAction.REPO_READ,
    "inspect_repository": RuntimeAction.REPO_READ,
    "read_repository": RuntimeAction.REPO_READ,
    "inspect_repo": RuntimeAction.REPO_READ,
    "collect_evidence": RuntimeAction.REPO_READ,
    "read_files": RuntimeAction.FILE_READ,
    "filesystem": RuntimeAction.FILE_READ,
    "context_packet_text": RuntimeAction.FILE_READ,
    "file_read": RuntimeAction.FILE_READ,
    "write_files": RuntimeAction.FILE_WRITE,
    "edit_files": RuntimeAction.FILE_WRITE,
    "file_write": RuntimeAction.FILE_WRITE,
    # shell / tests
    "inspect_runtime_state": RuntimeAction.SHELL_RUN,
    "run_read_only_commands": RuntimeAction.SHELL_RUN,
    "run_commands": RuntimeAction.SHELL_RUN,
    "run_read_commands": RuntimeAction.SHELL_RUN,
    "run_write_commands": RuntimeAction.SHELL_RUN,
    "shell": RuntimeAction.SHELL_RUN,
    "shell_run": RuntimeAction.SHELL_RUN,
    "run_tests": RuntimeAction.TEST_RUN,
    "test_runtime": RuntimeAction.TEST_RUN,
    "test_run": RuntimeAction.TEST_RUN,
    # git / publish
    "inspect_git": RuntimeAction.GIT_READ,
    "inspect_git_read_only": RuntimeAction.GIT_READ,
    "inspect_pr_checks": RuntimeAction.GIT_READ,
    "git_status": RuntimeAction.GIT_READ,
    "git": RuntimeAction.GIT_READ,
    "git_diff": RuntimeAction.GIT_READ,
    "git_read": RuntimeAction.GIT_READ,
    "commit_when_required": RuntimeAction.GIT_COMMIT,
    "commit": RuntimeAction.GIT_COMMIT,
    "git_commit": RuntimeAction.GIT_COMMIT,
    "push_when_required": RuntimeAction.GIT_PUSH,
    "push": RuntimeAction.GIT_PUSH,
    "git_push": RuntimeAction.GIT_PUSH,
    "git_push_--force": RuntimeAction.GIT_PUSH,
    "create_pr_when_required": RuntimeAction.PR_CREATE,
    "create_pr": RuntimeAction.PR_CREATE,
    "open_pull_request": RuntimeAction.PR_CREATE,
    "pull_request": RuntimeAction.PR_CREATE,
    # internet/freshness retrieval
    "internet_research": RuntimeAction.INTERNET_SEARCH,
    "read_official_docs": RuntimeAction.INTERNET_SEARCH,
    "inspect_public_metadata": RuntimeAction.INTERNET_SEARCH,
    "collect_source_attribution": RuntimeAction.INTERNET_SEARCH,
    "search_web": RuntimeAction.INTERNET_SEARCH,
    "internet_search": RuntimeAction.INTERNET_SEARCH,
    "web_search": RuntimeAction.INTERNET_SEARCH,
    "inspect_release_notes": RuntimeAction.INTERNET_SEARCH,
    "read_release_notes": RuntimeAction.INTERNET_SEARCH,
    "inspect_changelog": RuntimeAction.INTERNET_SEARCH,
    "inspect_package_registry": RuntimeAction.INTERNET_SEARCH,
    "read_package_registry": RuntimeAction.INTERNET_SEARCH,
    "resolve_package_versions": RuntimeAction.INTERNET_SEARCH,
    "version_resolution": RuntimeAction.INTERNET_SEARCH,
    "documentation_lookup": RuntimeAction.INTERNET_SEARCH,
    # infra / ops
    "k8s_read": RuntimeAction.K8S_READ,
    "kubectl_get": RuntimeAction.K8S_READ,
    "k8s_write": RuntimeAction.K8S_APPLY,
    "kubectl_apply": RuntimeAction.K8S_APPLY,
    "apply_cluster_changes": RuntimeAction.K8S_APPLY,
    "argocd_sync": RuntimeAction.ARGOCD_SYNC,
    "ssh": RuntimeAction.SSH_RUN,
    "ssh_run": RuntimeAction.SSH_RUN,
    "change_host_config": RuntimeAction.SSH_RUN,
})


STAGE_ALLOWED_RUNTIME_ACTIONS: Mapping[str, frozenset[RuntimeAction]] = MappingProxyType({
    "research": frozenset({RuntimeAction.INTERNET_SEARCH, RuntimeAction.REPO_READ, RuntimeAction.FILE_READ}),
    "observe": frozenset({RuntimeAction.REPO_READ, RuntimeAction.FILE_READ, RuntimeAction.SHELL_RUN, RuntimeAction.GIT_READ, RuntimeAction.K8S_READ}),
    "execute": frozenset({RuntimeAction.REPO_READ, RuntimeAction.FILE_READ, RuntimeAction.FILE_WRITE, RuntimeAction.SHELL_RUN, RuntimeAction.TEST_RUN, RuntimeAction.GIT_READ}),
    "repair": frozenset({RuntimeAction.REPO_READ, RuntimeAction.FILE_READ, RuntimeAction.FILE_WRITE, RuntimeAction.SHELL_RUN, RuntimeAction.TEST_RUN, RuntimeAction.GIT_READ}),
    "verify": frozenset({RuntimeAction.REPO_READ, RuntimeAction.FILE_READ, RuntimeAction.SHELL_RUN, RuntimeAction.TEST_RUN, RuntimeAction.GIT_READ, RuntimeAction.K8S_READ}),
    "publish": frozenset({RuntimeAction.REPO_READ, RuntimeAction.FILE_READ, RuntimeAction.SHELL_RUN, RuntimeAction.TEST_RUN, RuntimeAction.GIT_READ, RuntimeAction.GIT_COMMIT, RuntimeAction.GIT_PUSH, RuntimeAction.PR_CREATE}),
})


def normalize_runtime_action_token(value: object) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def allowed_runtime_actions_for_stage(stage: str | None) -> frozenset[RuntimeAction]:
    return STAGE_ALLOWED_RUNTIME_ACTIONS.get(str(stage or "").strip().lower(), frozenset())


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

    DEFAULT_STAGE_ALLOW: Mapping[str, frozenset[RuntimeAction]] = STAGE_ALLOWED_RUNTIME_ACTIONS

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
