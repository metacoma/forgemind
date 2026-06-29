from __future__ import annotations

import pytest

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.controller import WorkflowController
from artifact_workflow_runtime.llm_backend import ScriptedLLMBackend
from artifact_workflow_runtime.models import Capability, ExecutionFamily, Task
from artifact_workflow_runtime.openhands_adapter import FakeOpenHandsAdapter
from artifact_workflow_runtime.policy import StaticApprovalProvider

pytestmark = pytest.mark.asyncio


async def test_workflow_mvp_runs_end_to_end(tmp_path) -> None:
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    llm = ScriptedLLMBackend(
        {
            "classification": [
                {
                    "normalized_task": "Inspect repo and fix failing tests",
                    "needs_world_facts": True,
                    "execution_family": ExecutionFamily.REPOSITORY_CHANGE.value,
                    "capabilities": [Capability.REPO_READ.value, Capability.REPO_WRITE.value, Capability.GIT_WRITE.value],
                    "observation_focus": ["find failing test commands", "identify changed files"],
                    "reasoning": "Need repository facts before planning.",
                    "risk_level": "medium",
                }
            ],
            "planning": [
                {
                    "summary": "Edit failing code path and validate",
                    "execution_family": ExecutionFamily.REPOSITORY_CHANGE.value,
                    "capabilities": [Capability.REPO_WRITE.value, Capability.GIT_WRITE.value],
                    "steps": ["inspect failing path", "edit code", "run tests"],
                    "success_criteria": ["target tests pass"],
                    "verification_checks": ["run pytest target"],
                    "requires_mutation": True,
                    "reasoning": "Observation evidence indicates a concrete code fix is needed.",
                }
            ],
            "verification": [
                {
                    "passed": True,
                    "summary": "Evidence shows the target pytest command passed after the code change.",
                    "checks_passed": ["run pytest target"],
                    "checks_failed": [],
                    "missing_evidence": [],
                    "confidence": "high",
                    "reasoning": "Execution evidence includes a concrete file change and successful pytest output.",
                }
            ],
        }
    )
    openhands = FakeOpenHandsAdapter(
        artifact_store,
        scripts={
            "observe": ["Observed failing test: pytest tests/test_feature.py -k scenario. Relevant file: src/app.py"],
            "execute": ["Applied fix in src/app.py and ran pytest tests/test_feature.py -k scenario successfully."],
            "verify": ["unused in evidence-backed verification"],
        },
    )
    controller = WorkflowController(
        llm_backend=llm,
        openhands_adapter=openhands,
        artifact_root=tmp_path / "artifacts",
        approval_provider=StaticApprovalProvider(approve=True, reviewer="test"),
    )
    report = await controller.run(Task(description="Inspect repo metacoma/freeplane_plugin_grpc and fix failing tests"))
    assert report.status == "completed"
    assert report.classification is not None
    assert report.plan is not None
    assert report.policy is not None and report.policy.requires_approval is True
    assert report.approval is not None and report.approval.approved is True
    assert report.execution is not None
    assert report.verification is not None and report.verification.passed is True
    assert report.verification.checks_passed == ["run pytest target"]
    assert len(report.artifact_ids) >= 8
    assert len(openhands.calls["observe"]) == 1
    assert len(openhands.calls["execute"]) == 1
    assert len(openhands.calls["verify"]) == 0


async def test_repository_task_forces_observation_even_if_classifier_says_no_world_facts(tmp_path) -> None:
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    llm = ScriptedLLMBackend(
        {
            "classification": [
                {
                    "normalized_task": "Add C++ gRPC client",
                    "needs_world_facts": False,
                    "execution_family": ExecutionFamily.REPOSITORY_CHANGE.value,
                    "capabilities": [Capability.REPO_READ.value, Capability.REPO_WRITE.value],
                    "observation_focus": [],
                    "reasoning": "Classifier underestimates need for repo facts.",
                    "risk_level": "medium",
                }
            ],
            "planning": [
                {
                    "summary": "Implement the new client after inspecting existing clients",
                    "execution_family": ExecutionFamily.REPOSITORY_CHANGE.value,
                    "capabilities": [Capability.REPO_WRITE.value],
                    "steps": ["inspect repo", "implement client", "build"],
                    "success_criteria": ["new client exists", "build works"],
                    "verification_checks": ["confirm files exist", "confirm build output"],
                    "requires_mutation": True,
                    "reasoning": "Repo evidence is required and was collected before planning.",
                }
            ],
            "verification": [
                {
                    "passed": True,
                    "summary": "Evidence is sufficient.",
                    "checks_passed": ["confirm files exist", "confirm build output"],
                    "checks_failed": [],
                    "missing_evidence": [],
                    "confidence": "medium",
                    "reasoning": "Observation and execution evidence are both present.",
                }
            ],
        }
    )
    openhands = FakeOpenHandsAdapter(
        artifact_store,
        scripts={
            "observe": ["Found existing ruby/rust/python/nodejs clients and proto definitions in repo."],
            "execute": ["Added cpp client and build changes; cmake build succeeded."],
        },
    )
    controller = WorkflowController(
        llm_backend=llm,
        openhands_adapter=openhands,
        artifact_root=tmp_path / "artifacts",
        approval_provider=StaticApprovalProvider(approve=True, reviewer="test"),
    )
    report = await controller.run(Task(description="Work with repository metacoma/freeplane_plugin_grpc and add a C++ gRPC client"))
    assert report.status == "completed"
    assert len(openhands.calls["observe"]) == 1


async def test_html_execution_evidence_fails_verification_without_second_openhands_run(tmp_path) -> None:
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    llm = ScriptedLLMBackend(
        {
            "classification": [
                {
                    "normalized_task": "Modify repo",
                    "needs_world_facts": True,
                    "execution_family": ExecutionFamily.REPOSITORY_CHANGE.value,
                    "capabilities": [Capability.REPO_READ.value, Capability.REPO_WRITE.value],
                    "observation_focus": ["inspect repo"],
                    "reasoning": "Need repo facts.",
                    "risk_level": "medium",
                }
            ],
            "planning": [
                {
                    "summary": "Make repo change",
                    "execution_family": ExecutionFamily.REPOSITORY_CHANGE.value,
                    "capabilities": [Capability.REPO_WRITE.value],
                    "steps": ["edit file"],
                    "success_criteria": ["expected file changed"],
                    "verification_checks": ["confirm changed file", "confirm test output"],
                    "requires_mutation": True,
                    "reasoning": "Need mutation.",
                }
            ],
        }
    )
    openhands = FakeOpenHandsAdapter(
        artifact_store,
        scripts={
            "observe": ["Repo observed normally."],
            "execute": ["<!DOCTYPE html><html><body>OpenHands SPA</body></html>"],
        },
    )
    controller = WorkflowController(
        llm_backend=llm,
        openhands_adapter=openhands,
        artifact_root=tmp_path / "artifacts",
        approval_provider=StaticApprovalProvider(approve=True, reviewer="test"),
    )
    report = await controller.run(Task(description="Work with repository metacoma/freeplane_plugin_grpc and modify a file"))
    assert report.status == "execution_failed"
    assert report.execution is not None and report.execution.transport_error is True
    assert report.verification is not None and report.verification.passed is False
    assert "usable execution evidence" in report.verification.missing_evidence
    assert len(openhands.calls["verify"]) == 0


async def test_planner_cannot_degrade_implementation_task_into_documentation_only(tmp_path) -> None:
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    llm = ScriptedLLMBackend(
        {
            "classification": [
                {
                    "normalized_task": "Add C++ gRPC client",
                    "needs_world_facts": True,
                    "execution_family": ExecutionFamily.REPOSITORY_CHANGE.value,
                    "task_intent": "implement",
                    "capabilities": [Capability.REPO_READ.value, Capability.REPO_WRITE.value],
                    "observation_focus": ["inspect existing clients", "inspect build files"],
                    "reasoning": "User asked for a real implementation.",
                    "risk_level": "medium",
                }
            ],
            "planning": [
                {
                    "summary": "Analyze existing clients and document the steps to add C++ support",
                    "execution_family": ExecutionFamily.REPOSITORY_CHANGE.value,
                    "task_intent": "document",
                    "deliverable_kind": "documentation",
                    "capabilities": [Capability.REPO_WRITE.value],
                    "steps": ["analyze repo", "outline design", "provide instructions"],
                    "success_criteria": ["design is documented"],
                    "verification_checks": ["confirm instructions exist"],
                    "requires_mutation": False,
                    "must_change_world": False,
                    "expected_repo_changes": [],
                    "reasoning": "Planner degraded the task into documentation.",
                }
            ],
        }
    )
    openhands = FakeOpenHandsAdapter(
        artifact_store,
        scripts={
            "observe": ["Found ruby/rust/python/nodejs clients and proto/build files."],
            "execute": ["should not run"],
        },
    )
    controller = WorkflowController(
        llm_backend=llm,
        openhands_adapter=openhands,
        artifact_root=tmp_path / "artifacts",
        approval_provider=StaticApprovalProvider(approve=True, reviewer="test"),
    )
    report = await controller.run(Task(description="Work with repository metacoma/freeplane_plugin_grpc and add a C++ gRPC client"))
    assert report.status == "blocked"
    assert report.policy is not None and report.policy.blocked is True
    assert any("degraded" in reason.lower() or "documentation" in reason.lower() for reason in report.policy.reasons)
    assert len(openhands.calls["execute"]) == 0
