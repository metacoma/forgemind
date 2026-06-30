from __future__ import annotations

import pytest

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.controller import WorkflowController
from artifact_workflow_runtime.llm_backend import ScriptedLLMBackend
from artifact_workflow_runtime.models import Capability, ExecutionFamily, Task
from artifact_workflow_runtime.openhands_adapter import FakeOpenHandsAdapter
from artifact_workflow_runtime.policy import StaticApprovalProvider

pytestmark = pytest.mark.asyncio


async def test_done_contract_compiles_before_obligations_and_survives_into_final_report(tmp_path) -> None:
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    llm = ScriptedLLMBackend({
        "classification": [{
            "normalized_task": "Add C# gRPC client",
            "needs_world_facts": True,
            "execution_family": ExecutionFamily.REPOSITORY_CHANGE.value,
            "task_intent": "implement",
            "capabilities": [Capability.REPO_READ.value, Capability.REPO_WRITE.value],
            "observation_focus": ["inspect existing clients", "inspect workflows"],
            "reasoning": "Need repository facts before planning.",
            "risk_level": "medium",
        }],
        "route_analysis": [{
            "needs_repository_observation": True,
            "needs_world_observation": False,
            "needs_fresh_external_research": False,
            "can_plan_immediately": False,
            "required_evidence_types": ["repo_patterns"],
            "research_targets": [],
            "observation_focus": ["inspect existing clients", "inspect workflows"],
            "reasoning": "Need repository observation before planning.",
        }],
        "obligation_analysis": [{
            "required_test_levels": ["build", "integration"],
            "required_setup_steps": ["Use repository-supported Freeplane bootstrap if present"],
            "required_environment_conditions": ["freeplane_runtime"],
            "required_ci_updates": ["wire new integration checks into GitHub Actions"],
            "required_publish_actions": ["commit", "push", "create_pr"],
            "completion_requirements": ["client implementation", "runtime proof", "integration coverage"],
            "blocker_conditions": [],
            "reasoning_summary": "New client changes require runtime proof and CI wiring.",
        }],
        "planning": [{
            "summary": "Implement new client and validate it",
            "execution_family": ExecutionFamily.REPOSITORY_CHANGE.value,
            "task_intent": "implement",
            "deliverable_kind": "repository_changes",
            "capabilities": [Capability.REPO_WRITE.value],
            "steps": ["implement client", "run verification"],
            "success_criteria": ["client exists", "runtime proof exists"],
            "verification_checks": ["run repository verification target"],
            "requires_mutation": True,
            "must_change_world": True,
            "expected_repo_changes": ["grpc/csharp/**"],
            "reasoning": "Use repository patterns and done contract.",
        }],
        "verification": [{
            "passed": True,
            "summary": "QA evidence passed.",
            "checks_passed": ["run repository verification target"],
            "checks_failed": [],
            "missing_evidence": [],
            "confidence": "high",
            "reasoning": "Enough evidence.",
        }],
    })
    openhands = FakeOpenHandsAdapter(
        artifact_store,
        scripts={
            "observe": ["Found grpc/python and grpc/kotlin clients and scripts/install_freeplane.sh in the repository."],
            "execute": ["Added grpc/csharp client and ran local checks."],
        },
    )
    controller = WorkflowController(
        llm_backend=llm,
        openhands_adapter=openhands,
        artifact_root=tmp_path / "artifacts",
        approval_provider=StaticApprovalProvider(approve=True, reviewer="test"),
    )
    report = await controller.run(Task(description="Add a C# gRPC client using existing repository patterns"))
    assert report.status in {"completed", "implemented_only", "accepted_with_verification_debt", "needs_human_review", "needs_environment", "partially_completed"}
    assert report.done_contract is not None
    assert report.done_contract["change_class"] == "new_client_integration"
    assert "runtime_proof" in report.done_contract["deliverables"]
    artifact_kinds = []
    for artifact_id in report.artifact_ids:
        meta = artifact_store.get(artifact_id)
        artifact_kinds.append(meta.kind)
    assert "done_contract" in artifact_kinds
    assert artifact_kinds.index("done_contract") < artifact_kinds.index("obligation_analysis")
