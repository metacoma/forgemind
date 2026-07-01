from __future__ import annotations

import pytest

from artifact_workflow_runtime.models import (
    BackendKind,
    ExecutionFamily,
    ObservationRequest,
    PublishRequest,
    VerificationMode,
    VerificationRequest,
)
from artifact_workflow_runtime.policy.action_policy import ActionPolicyEnforcer


def test_observe_stage_cannot_allow_mutation() -> None:
    enforcer = ActionPolicyEnforcer()
    request = ObservationRequest(
        task_id="task",
        execution_family=ExecutionFamily.REPOSITORY_CHANGE,
        prompt="observe",
        allowed_actions=["repo.read", "file.write"],
        forbidden_actions=["publish", "create_pr", "open_pull_request", "commit", "push", "git push", "edit_files", "write_files"],
    )
    with pytest.raises(ValueError):
        enforcer.validate_request(request, stage="observe", label="Observe")


def test_verify_stage_cannot_allow_publish_or_mutation() -> None:
    enforcer = ActionPolicyEnforcer()
    request = VerificationRequest(
        execution_result_id="exec",
        execution_family=ExecutionFamily.REPOSITORY_CHANGE,
        backend=BackendKind.OPENHANDS,
        mode=VerificationMode.WORLD_CHECK,
        prompt="verify",
        checks=["pytest"],
        allowed_inputs=["filesystem", "shell", "git", "test_runtime", "context_packet_text", "publish"],
        forbidden_inputs=[
            "change_workflow_decision",
            "declare_task_completed_or_accepted",
            "expand_task_scope",
            "edit_files",
            "write_files",
            "fix_code",
            "repair",
            "commit",
            "push",
            "git push",
            "create_pr",
            "open_pull_request",
            "publish",
        ],
    )
    with pytest.raises(ValueError):
        enforcer.validate_request(request, stage="verify", label="Verify")


def test_publish_stage_allows_publish_actions_but_requires_scope_guards() -> None:
    enforcer = ActionPolicyEnforcer()
    request = PublishRequest(
        execution_result_id="exec",
        task_id="task",
        prompt="publish",
        allowed_actions=["git.commit", "git.push", "pr.create"],
        forbidden_actions=["repair", "reimplement_feature", "expand_task_scope"],
    )

    enforcer.validate_request(request, stage="publish", label="Publish")
