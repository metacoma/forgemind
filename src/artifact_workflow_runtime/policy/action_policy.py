from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from artifact_workflow_runtime.policy.acl import RuntimeAction


MUTATING_RUNTIME_ACTIONS = {RuntimeAction.FILE_WRITE, RuntimeAction.GIT_COMMIT, RuntimeAction.GIT_PUSH, RuntimeAction.K8S_APPLY, RuntimeAction.ARGOCD_SYNC, RuntimeAction.SSH_RUN}
PUBLISH_RUNTIME_ACTIONS = {RuntimeAction.GIT_COMMIT, RuntimeAction.GIT_PUSH, RuntimeAction.PR_CREATE}
READ_ONLY_RUNTIME_ACTIONS = {RuntimeAction.REPO_READ, RuntimeAction.FILE_READ, RuntimeAction.SHELL_RUN, RuntimeAction.TEST_RUN, RuntimeAction.GIT_READ, RuntimeAction.INTERNET_SEARCH, RuntimeAction.K8S_READ}


@dataclass(frozen=True)
class StageActionPolicyProfile:
    stage: str
    allow_mutation: bool
    allow_publish_actions: bool
    required_forbidden_tokens: frozenset[str]


DEFAULT_STAGE_ACTION_POLICIES: dict[str, StageActionPolicyProfile] = {
    "research": StageActionPolicyProfile("research", allow_mutation=False, allow_publish_actions=False, required_forbidden_tokens=frozenset({"edit_files", "write_files", "publish", "create_pr", "open_pull_request", "commit", "push", "git push"})),
    "observe": StageActionPolicyProfile("observe", allow_mutation=False, allow_publish_actions=False, required_forbidden_tokens=frozenset({"edit_files", "write_files", "publish", "create_pr", "open_pull_request", "commit", "push", "git push"})),
    "verify": StageActionPolicyProfile("verify", allow_mutation=False, allow_publish_actions=False, required_forbidden_tokens=frozenset({"publish", "create_pr", "open_pull_request", "commit", "push", "git push", "edit_files", "write_files", "repair"})),
    "execute": StageActionPolicyProfile("execute", allow_mutation=True, allow_publish_actions=False, required_forbidden_tokens=frozenset({"publish", "create_pr", "open_pull_request", "commit", "push", "git push"})),
    "repair": StageActionPolicyProfile("repair", allow_mutation=True, allow_publish_actions=False, required_forbidden_tokens=frozenset({"publish", "create_pr", "open_pull_request", "commit", "push", "git push"})),
    "publish": StageActionPolicyProfile("publish", allow_mutation=True, allow_publish_actions=True, required_forbidden_tokens=frozenset({"repair", "reimplement_feature", "expand_task_scope"})),
}


class ActionPolicyEnforcer:
    def __init__(self, profiles: dict[str, StageActionPolicyProfile] | None = None) -> None:
        self.profiles = dict(profiles or DEFAULT_STAGE_ACTION_POLICIES)

    def validate_request(self, request: object, *, stage: str, label: str) -> None:
        profile = self.profiles.get(stage, StageActionPolicyProfile(stage, allow_mutation=False, allow_publish_actions=False, required_forbidden_tokens=frozenset()))
        allowed = {_normalize_runtime_action(item) for item in [*getattr(request, "allowed_actions", []), *getattr(request, "allowed_inputs", [])]}
        forbidden_tokens = {_normalize_token(item) for item in [*getattr(request, "forbidden_actions", []), *getattr(request, "forbidden_inputs", [])]}
        unknown = {action for action in allowed if action == RuntimeAction.UNKNOWN}
        if unknown:
            raise ValueError(f"{label} packets cannot allow unknown/unclassified runtime actions for stage {stage}.")
        if not profile.allow_mutation:
            illegal = {action for action in allowed if action in MUTATING_RUNTIME_ACTIONS}
            if illegal:
                raise ValueError(f"{label} packets cannot allow mutating actions for stage {stage}: {sorted(action.value for action in illegal)}")
        if not profile.allow_publish_actions:
            illegal = {action for action in allowed if action in PUBLISH_RUNTIME_ACTIONS}
            if illegal:
                raise ValueError(f"{label} packets cannot allow publish actions for stage {stage}: {sorted(action.value for action in illegal)}")
        missing = profile.required_forbidden_tokens - forbidden_tokens
        if missing:
            raise ValueError(f"{label} packets must explicitly forbid {sorted(missing)} for stage {stage}")



def _normalize_runtime_action(value: object) -> RuntimeAction:
    return RuntimeAction.coerce(value)


def _normalize_token(value: object) -> str:
    return str(value).strip().lower()
