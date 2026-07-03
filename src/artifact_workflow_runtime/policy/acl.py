from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
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
        text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "read_repo": cls.REPO_READ,
            "repo_read": cls.REPO_READ,
            "inspect_repository": cls.REPO_READ,
            "read_repository": cls.REPO_READ,
            "inspect_repo": cls.REPO_READ,
            "collect_evidence": cls.REPO_READ,
            "inspect_runtime_state": cls.SHELL_RUN,
            "run_read_only_commands": cls.SHELL_RUN,
            "inspect_git": cls.GIT_READ,
            "inspect_git_read_only": cls.GIT_READ,
            "commit_when_required": cls.GIT_COMMIT,
            "push_when_required": cls.GIT_PUSH,
            "create_pr_when_required": cls.PR_CREATE,
            "inspect_pr_checks": cls.GIT_READ,
            "internet_research": cls.INTERNET_SEARCH,
            "read_official_docs": cls.INTERNET_SEARCH,
            "inspect_public_metadata": cls.INTERNET_SEARCH,
            "collect_source_attribution": cls.INTERNET_SEARCH,
            "read_files": cls.FILE_READ,
            "filesystem": cls.FILE_READ,
            "context_packet_text": cls.FILE_READ,
            "file_read": cls.FILE_READ,
            "write_files": cls.FILE_WRITE,
            "edit_files": cls.FILE_WRITE,
            "file_write": cls.FILE_WRITE,
            "run_commands": cls.SHELL_RUN,
            "run_read_commands": cls.SHELL_RUN,
            "run_write_commands": cls.SHELL_RUN,
            "shell": cls.SHELL_RUN,
            "shell_run": cls.SHELL_RUN,
            "run_tests": cls.TEST_RUN,
            "test_runtime": cls.TEST_RUN,
            "test_run": cls.TEST_RUN,
            "git_status": cls.GIT_READ,
            "git": cls.GIT_READ,
            "git_diff": cls.GIT_READ,
            "git_read": cls.GIT_READ,
            "commit": cls.GIT_COMMIT,
            "git_commit": cls.GIT_COMMIT,
            "push": cls.GIT_PUSH,
            "git_push": cls.GIT_PUSH,
            "git_push_--force": cls.GIT_PUSH,
            "create_pr": cls.PR_CREATE,
            "open_pull_request": cls.PR_CREATE,
            "pull_request": cls.PR_CREATE,
            "search_web": cls.INTERNET_SEARCH,
            "internet_search": cls.INTERNET_SEARCH,
            "web_search": cls.INTERNET_SEARCH,
            "k8s_read": cls.K8S_READ,
            "kubectl_get": cls.K8S_READ,
            "k8s_write": cls.K8S_APPLY,
            "kubectl_apply": cls.K8S_APPLY,
            "apply_cluster_changes": cls.K8S_APPLY,
            "argocd_sync": cls.ARGOCD_SYNC,
            "ssh": cls.SSH_RUN,
            "ssh_run": cls.SSH_RUN,
            "change_host_config": cls.SSH_RUN,
        }
        if text in {item.value for item in cls}:
            return cls(text)
        return aliases.get(text, cls.UNKNOWN)


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
        "research": frozenset({RuntimeAction.INTERNET_SEARCH, RuntimeAction.REPO_READ, RuntimeAction.FILE_READ}),
        "observe": frozenset({RuntimeAction.REPO_READ, RuntimeAction.FILE_READ, RuntimeAction.SHELL_RUN, RuntimeAction.GIT_READ, RuntimeAction.K8S_READ}),
        "execute": frozenset({RuntimeAction.REPO_READ, RuntimeAction.FILE_READ, RuntimeAction.FILE_WRITE, RuntimeAction.SHELL_RUN, RuntimeAction.TEST_RUN, RuntimeAction.GIT_READ}),
        "repair": frozenset({RuntimeAction.REPO_READ, RuntimeAction.FILE_READ, RuntimeAction.FILE_WRITE, RuntimeAction.SHELL_RUN, RuntimeAction.TEST_RUN, RuntimeAction.GIT_READ}),
        "verify": frozenset({RuntimeAction.REPO_READ, RuntimeAction.FILE_READ, RuntimeAction.SHELL_RUN, RuntimeAction.TEST_RUN, RuntimeAction.GIT_READ, RuntimeAction.K8S_READ}),
        "publish": frozenset({RuntimeAction.REPO_READ, RuntimeAction.FILE_READ, RuntimeAction.SHELL_RUN, RuntimeAction.TEST_RUN, RuntimeAction.GIT_READ, RuntimeAction.GIT_COMMIT, RuntimeAction.GIT_PUSH, RuntimeAction.PR_CREATE}),
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
