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
        }
    )
    openhands = FakeOpenHandsAdapter(
        artifact_store,
        scripts={
            "observe": ["Observed failing test: pytest tests/test_feature.py -k scenario. Relevant file: src/app.py"],
            "execute": ["Applied fix in src/app.py and ran pytest tests/test_feature.py -k scenario successfully."],
            "verify": ["PASS: target pytest command succeeded and modified file matches the requested fix."],
        },
    )
    controller = WorkflowController(
        llm_backend=llm,
        openhands_adapter=openhands,
        artifact_root=tmp_path / "artifacts",
        approval_provider=StaticApprovalProvider(approve=True, reviewer="test"),
    )
    report = await controller.run(Task(description="Inspect repo and fix failing tests", repository="owner/repo", git_provider="github"))
    assert report.status == "completed"
    assert report.classification is not None
    assert report.plan is not None
    assert report.policy is not None and report.policy.requires_approval is True
    assert report.approval is not None and report.approval.approved is True
    assert report.execution is not None
    assert report.verification is not None and report.verification.passed is True
    assert len(report.artifact_ids) >= 6
    assert len(openhands.calls["observe"]) == 1
    assert len(openhands.calls["execute"]) == 1
    assert len(openhands.calls["verify"]) == 1
