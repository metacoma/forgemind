from __future__ import annotations

import pytest

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.controller import WorkflowController
from artifact_workflow_runtime.llm_backend import ScriptedLLMBackend
from artifact_workflow_runtime.models import Capability, ExecutionFamily, Task
from artifact_workflow_runtime.openhands_adapter import FakeOpenHandsAdapter
from artifact_workflow_runtime.policy import StaticApprovalProvider

pytestmark = pytest.mark.asyncio


def _llm_scripts() -> dict[str, list[dict[str, object]]]:
    return {
        "classification": [
            {
                "normalized_task": "Modify repository file",
                "needs_world_facts": True,
                "execution_family": ExecutionFamily.REPOSITORY_CHANGE.value,
                "task_intent": "modify",
                "capabilities": [Capability.REPO_READ.value, Capability.REPO_WRITE.value],
                "observation_focus": ["repo facts"],
                "reasoning": "Need repo facts.",
            }
        ],
        "route_analysis": [
            {
                "needs_repository_observation": True,
                "needs_world_observation": False,
                "needs_fresh_external_research": False,
                "can_plan_immediately": False,
                "required_evidence_types": ["repo"],
                "research_targets": [],
                "observation_focus": ["repo facts"],
                "reasoning": "Need observation.",
            }
        ],
        "obligation_analysis": [
            {
                "required_test_levels": ["unit"],
                "required_setup_steps": [],
                "required_environment_conditions": ["docker_container"],
                "completion_requirements": ["file changed and test run"],
                "blocker_conditions": [],
                "reasoning_summary": "Mutation requires verification.",
            }
        ],
        "planning": [
            {
                "summary": "Edit file and run tests",
                "execution_family": ExecutionFamily.REPOSITORY_CHANGE.value,
                "task_intent": "modify",
                "deliverable_kind": "repository_changes",
                "capabilities": [Capability.REPO_WRITE.value],
                "steps": ["edit file", "run test"],
                "success_criteria": ["file changed", "test passed"],
                "verification_checks": ["unit tests passed"],
                "requires_mutation": True,
                "must_change_world": True,
                "expected_repo_changes": ["src/app.py"],
                "reasoning": "Need mutation.",
            }
        ],
    }


async def test_empty_openhands_execute_result_retries_then_stops_before_verification(tmp_path) -> None:
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    controller = WorkflowController(
        llm_backend=ScriptedLLMBackend(_llm_scripts()),
        openhands_adapter=FakeOpenHandsAdapter(artifact_store, scripts={"observe": ["Repo observed."], "execute": ["", "", "", ""]}),
        artifact_root=tmp_path / "artifacts",
        approval_provider=StaticApprovalProvider(approve=True, reviewer="test"),
    )

    report = await controller.run(Task(description="Modify repo file"))

    assert report.status == "agent_failed"
    assert report.execution is not None
    assert report.execution.stage_failure is not None
    assert report.execution.stage_failure.failure_kind.value == "agent_no_result"
    assert report.verification is None
    assert len(controller.services.openhands_adapter.calls["execute"]) == 4
    retry_artifacts = [a for a in artifact_store.list() if a.kind == "agent_retry_decision"]
    assert len(retry_artifacts) == 3
    last_retry = artifact_store.read_json(retry_artifacts[-1].id)
    assert last_retry["next_retry_count"] == 3
    snapshots = [a for a in artifact_store.list() if a.kind == "workflow_state_snapshot"]
    assert snapshots
    snapshot = artifact_store.read_json(snapshots[-1].id)
    assert snapshot["agent_retry_count"] == 3
    assert len(snapshot["agent_retry_history"]) == 3


async def test_retryable_execute_agent_failure_can_recover_on_retry(tmp_path) -> None:
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    scripts = _llm_scripts()
    scripts["verification"] = [
        {
            "passed": True,
            "summary": "Retry recovered and evidence is sufficient.",
            "checks_passed": ["unit tests passed"],
            "checks_failed": [],
            "missing_evidence": [],
            "confidence": "high",
            "reasoning": "Execution evidence includes changed file and passing test.",
            "performed_test_levels": ["unit"],
        }
    ]
    controller = WorkflowController(
        llm_backend=ScriptedLLMBackend(scripts),
        openhands_adapter=FakeOpenHandsAdapter(
            artifact_store,
            scripts={
                "observe": ["Repo observed."],
                "execute": ["", "Changed file src/app.py. Command: pytest tests/test_app.py passed."],
            },
        ),
        artifact_root=tmp_path / "artifacts",
        approval_provider=StaticApprovalProvider(approve=True, reviewer="test"),
    )

    report = await controller.run(Task(description="Modify repo file"))

    assert report.status == "completed"
    assert report.execution is not None
    assert report.execution.stage_failure is None
    assert len(controller.services.openhands_adapter.calls["execute"]) == 2
    retry_artifacts = [a for a in artifact_store.list() if a.kind == "agent_retry_decision"]
    assert len(retry_artifacts) == 1
    retry = artifact_store.read_json(retry_artifacts[0].id)
    assert retry["retry_allowed"] is True
    assert retry["failure_kind"] == "agent_no_result"


async def test_empty_observation_result_stops_before_context_build(tmp_path) -> None:
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    controller = WorkflowController(
        llm_backend=ScriptedLLMBackend(_llm_scripts()),
        openhands_adapter=FakeOpenHandsAdapter(artifact_store, scripts={"observe": [""]}),
        artifact_root=tmp_path / "artifacts",
        approval_provider=StaticApprovalProvider(approve=True, reviewer="test"),
    )

    report = await controller.run(Task(description="Modify repo file"))

    assert report.status == "observation_failed"
    assert report.observation is not None
    assert report.observation.stage_failure is not None
    assert report.plan is None
    assert report.execution is None
