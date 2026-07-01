from __future__ import annotations

import pytest

from artifact_workflow_runtime.models import (
    BackendKind,
    Capability,
    ExecutionFamily,
    ExecutionRequest,
    ObservationRequest,
    PublishRequest,
    RepairRequest,
    VerificationMode,
    VerificationRequest,
    WorkPacketKind,
)
from artifact_workflow_runtime.observation.service import ObservationService
from artifact_workflow_runtime.models import RoutingDecision, Task, TaskClassification
from artifact_workflow_runtime.openhands_adapter.adapter import OpenHandsAdapter


def _assert_stage_contract(prompt: str) -> None:
    assert "## Non-negotiable control-plane boundary" in prompt
    assert "## Allowed actions" in prompt
    assert "## Forbidden actions" in prompt
    assert "## Stop conditions" in prompt
    assert "## Required outputs" in prompt
    assert "Do not choose the next workflow step" in prompt
    assert "if an action is not explicitly allowed, treat it as forbidden" in prompt
    assert "Return exactly one JSON object" not in prompt
    assert "response_format: json" not in prompt
    assert "BEGIN_JSON_SCHEMA" not in prompt
    assert "END_JSON_SCHEMA" not in prompt
    assert "OpenHandsMachineHandoff" not in prompt
    assert "machine_json_handoff_schema" not in prompt
    assert "First OpenHands pass must return a concise human-readable operational report only" in prompt
    assert "the controller will request the canonical JSON handoff in a separate follow-up" in prompt
    assert "Hard editing policy" in prompt
    assert "never rewrite large files or large sections in one pass" in prompt
    assert "about 2 KB" in prompt
    assert "small local patch-style edit units" in prompt
    assert "Do not replace an entire file unless it is strictly unavoidable" in prompt


def test_observe_prompt_is_read_only_and_forbids_git_mutation() -> None:
    request = ObservationRequest(
        task_id="task_1",
        execution_family=ExecutionFamily.REPOSITORY_CHANGE,
        capabilities=[Capability.REPO_READ, Capability.GIT_READ],
        prompt="Collect repository facts only.",
    )
    compiled = request.compiled_prompt()
    _assert_stage_contract(compiled)
    assert "read-only fact collection" in compiled
    assert "edit_files" in compiled
    assert "git push" in compiled
    assert "create_pr" in compiled
    assert "publish" in compiled
    assert "global plan creation" in compiled


def test_observation_service_filters_mutating_capabilities() -> None:
    request = ObservationService().build_request(
        Task(description="Inspect repo before implementation."),
        TaskClassification(
            normalized_task="Inspect repo before implementation.",
            needs_world_facts=True,
            execution_family=ExecutionFamily.REPOSITORY_CHANGE,
            task_intent="implement",
            capabilities=[Capability.REPO_READ, Capability.REPO_WRITE, Capability.GIT_WRITE, Capability.REPO_CREATE_PR],
            observation_focus=["repo layout"],
            reasoning="Need repository facts.",
        ),
        RoutingDecision(needs_repository_observation=True, observation_focus=["tests"], reasoning="Need repo facts."),
    )
    assert request.capabilities == [Capability.REPO_READ]
    assert "git push" in request.compiled_prompt()


def test_execute_prompt_forbids_git_push_without_publish_packet() -> None:
    request = ExecutionRequest(
        task_id="task_1",
        execution_family=ExecutionFamily.REPOSITORY_CHANGE,
        capabilities=[Capability.REPO_WRITE, Capability.GIT_WRITE],
        prompt="Implement the controller-approved change.",
    )
    compiled = request.compiled_prompt()
    _assert_stage_contract(compiled)
    assert "bounded implementation/execution" in compiled
    assert "git push" in compiled
    assert "git push --force" in compiled
    assert "git tag" in compiled
    assert "git merge" in compiled
    assert "git rebase" in compiled
    assert "create_pr" in compiled
    assert "commit/push/PR publication" in compiled
    assert "Runtime/bootstrap obligation" in compiled
    assert "syntax checks, compile-only/build-only evidence, script existence" in compiled
    assert "Found scripts alone are not setup success" in compiled


def test_execute_contract_validator_rejects_missing_git_push_guard() -> None:
    request = ExecutionRequest(
        task_id="task_1",
        execution_family=ExecutionFamily.REPOSITORY_CHANGE,
        capabilities=[Capability.REPO_WRITE],
        prompt="Implement the change.",
        forbidden_actions=["change_workflow_decision", "commit", "create_pr", "open_pull_request", "publish"],
    )
    with pytest.raises(ValueError, match="git push"):
        OpenHandsAdapter._validate_execution_contract(request)


def test_world_verify_prompt_forbids_mutation_and_acceptance_decisions() -> None:
    request = VerificationRequest(
        execution_result_id="exec_1",
        execution_family=ExecutionFamily.REPOSITORY_CHANGE,
        backend=BackendKind.OPENHANDS,
        mode=VerificationMode.WORLD_CHECK,
        prompt="Run only requested integration checks.",
        checks=["integration tests"],
        allowed_inputs=["filesystem", "shell", "git", "test_runtime", "context_packet_text"],
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
            "git push --force",
            "git tag",
            "git merge",
            "git rebase",
            "create_pr",
            "open_pull_request",
            "publish",
            "release",
        ],
    )
    compiled = request.compiled_prompt()
    _assert_stage_contract(compiled)
    assert "bounded world verification" in compiled
    assert "fix_code" in compiled
    assert "final acceptance decision" in compiled
    OpenHandsAdapter._validate_world_verification_contract(request)


def test_publish_prompt_allows_only_publication_and_forbids_repair() -> None:
    request = PublishRequest(
        execution_result_id="exec_1",
        task_id="task_1",
        prompt="Publish only controller-approved changes.",
        require_commit=True,
        require_push=True,
    )
    compiled = request.compiled_prompt()
    _assert_stage_contract(compiled)
    assert "bounded repository publication" in compiled
    assert "push_when_required" in compiled
    assert "fix_ci_after_publish" in compiled
    assert "repair" in compiled
    assert "edit_source_files" in compiled
    assert "git push --force" in compiled
    assert "CI repair" in compiled
    OpenHandsAdapter._validate_publish_contract(request)


def test_repair_prompt_forbids_publish_and_pr_actions() -> None:
    request = RepairRequest(
        task_id="task_1",
        execution_result_id="exec_1",
        publish_result_id="publish_1",
        execution_family=ExecutionFamily.REPOSITORY_CHANGE,
        prompt="Repair only the controller-provided failed check.",
        failed_checks=["ci/test"],
        attempt=1,
        max_attempts=2,
    )
    compiled = request.compiled_prompt()
    _assert_stage_contract(compiled)
    assert "bounded repair" in compiled
    assert "git push" in compiled
    assert "create_pr" in compiled
    assert "publish" in compiled
    assert "unrelated refactor" in compiled
    OpenHandsAdapter._validate_repair_contract(request)
