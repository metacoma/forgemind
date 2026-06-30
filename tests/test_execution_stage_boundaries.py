from __future__ import annotations

from artifact_workflow_runtime.models import Capability, ExecutionFamily, ExecutionPlan
from artifact_workflow_runtime.control_plane.stage_filters import (
    execute_prompt_steps,
    execute_success_criteria,
    execute_verification_commands,
)


def _plan() -> ExecutionPlan:
    return ExecutionPlan(
        summary="Implement code and publish later",
        execution_family=ExecutionFamily.REPOSITORY_CHANGE,
        task_intent="implement",
        deliverable_kind="repository_changes",
        capabilities=[Capability.REPO_WRITE, Capability.REPO_CREATE_PR],
        steps=[
            "edit code",
            "run unit tests",
            "push changes to branch and open pull request",
            "wait_pr_checks",
        ],
        success_criteria=[
            "unit tests pass",
            "integration tests pass",
            "PR checks green",
            "changes pushed to remote",
        ],
        verification_checks=[
            "unit tests pass",
            "integration tests pass",
            "wait_pr_checks",
            "pull request exists",
        ],
        requires_mutation=True,
        must_change_world=True,
        expected_repo_changes=["src/app.py"],
        require_commit=True,
        require_push=True,
        publication_steps=["commit", "push", "create_pr", "wait_pr_checks"],
        reasoning="Publication belongs to publish stage.",
    )


def test_execute_stage_filters_publish_obligations_from_request_fields() -> None:
    plan = _plan()

    assert execute_prompt_steps(plan) == ["edit code", "run unit tests"]
    assert execute_success_criteria(plan) == ["unit tests pass", "integration tests pass"]
    assert execute_verification_commands(plan) == ["unit tests pass", "integration tests pass"]
