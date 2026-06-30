from __future__ import annotations

import pytest

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.controller import WorkflowController
from artifact_workflow_runtime.control_plane import RuntimeKernel
from artifact_workflow_runtime.lifecycle import PipelineReentryTarget, PipelineLoopTriggerKind
from artifact_workflow_runtime.llm_backend.fake import ScriptedLLMBackend
from artifact_workflow_runtime.models import (
    AcceptanceObligationKind,
    AcceptanceStatus,
    Capability,
    ExecutionFamily,
    ExecutionPlan,
    ExecutionResult,
    ExecutionStatus,
    ObligationAnalysis,
    Task,
    TaskClassification,
    VerificationResult,
)
from artifact_workflow_runtime.openhands_adapter.fake import FakeOpenHandsAdapter
from artifact_workflow_runtime.policy import StaticApprovalProvider


def _plan() -> ExecutionPlan:
    return ExecutionPlan(
        summary="Implement feature",
        execution_family=ExecutionFamily.REPOSITORY_CHANGE,
        task_intent="implement",
        deliverable_kind="repository_changes",
        capabilities=[Capability.REPO_WRITE],
        steps=["edit code", "run tests"],
        success_criteria=["feature works"],
        verification_checks=["unit tests pass"],
        requires_mutation=True,
        must_change_world=True,
        expected_repo_changes=["src/app.py"],
        required_test_levels=["unit"],
        reasoning="test plan",
    )


def test_kernel_reenters_obligation_discovery_when_verification_discovers_docs_impact() -> None:
    kernel = RuntimeKernel()
    verification = VerificationResult(
        request_id="verify_req",
        passed=False,
        summary="Implementation exists, but documentation impact discovered: README update is missing.",
        evidence_text="evidence summary",
        missing_obligations=["documentation impact discovered: README update required"],
        completion_status="partially_completed",
    )

    decision = kernel.evaluate_pipeline_reentry(source_stage="verify", plan=_plan(), verification=verification)

    assert decision.allowed is True
    assert decision.automatic is True
    assert decision.trigger_kind == PipelineLoopTriggerKind.DOCS_IMPACT_DISCOVERED
    assert decision.target_stage == PipelineReentryTarget.OBLIGATIONS


def test_kernel_stops_reentry_when_budget_is_exhausted() -> None:
    kernel = RuntimeKernel()
    verification = VerificationResult(
        request_id="verify_req",
        passed=False,
        summary="Documentation impact discovered: README update is missing.",
        evidence_text="evidence summary",
        missing_obligations=["documentation impact discovered: README update required"],
        completion_status="partially_completed",
    )
    first = kernel.evaluate_pipeline_reentry(source_stage="verify", plan=_plan(), verification=verification)
    second = kernel.evaluate_pipeline_reentry(source_stage="verify", plan=_plan(), verification=verification, loop_decisions=[first])

    assert first.target_stage == PipelineReentryTarget.OBLIGATIONS
    assert second.allowed is False
    assert second.budget_exhausted is True
    assert second.target_stage == PipelineReentryTarget.FINALIZE


def test_acceptance_contract_includes_discovered_docs_examples_ci_codegen_obligations() -> None:
    kernel = RuntimeKernel()
    task = Task(description="Add public API")
    classification = TaskClassification(
        normalized_task="Add public API",
        needs_world_facts=True,
        execution_family=ExecutionFamily.REPOSITORY_CHANGE,
        task_intent="implement",
        capabilities=[Capability.REPO_READ, Capability.REPO_WRITE],
        reasoning="repo change",
    )
    obligations = ObligationAnalysis(
        required_documentation_updates=["README public API section"],
        required_examples_updates=["usage snippet"],
        required_ci_updates=["CI job for generated client"],
        required_codegen_or_build_updates=["proto codegen target"],
        affected_surfaces=["public API", "generated client"],
        completion_requirements=["API remains backwards compatible"],
        reasoning_summary="Public API change broadens work surface.",
    )

    contract = kernel.build_acceptance_contract(task=task, classification=classification, plan=_plan(), obligations=obligations)
    kinds = {item.kind for item in contract.obligations}

    assert AcceptanceObligationKind.DOCUMENTATION_UPDATED in kinds
    assert AcceptanceObligationKind.EXAMPLES_UPDATED in kinds
    assert AcceptanceObligationKind.CI_OR_BUILD_UPDATED in kinds
    assert AcceptanceObligationKind.CODEGEN_OR_TOOLING_UPDATED in kinds
    assert AcceptanceObligationKind.WORK_SURFACE_COMPLETE in kinds


@pytest.mark.asyncio
async def test_workflow_reenters_obligations_after_verification_discovers_docs_gap(tmp_path) -> None:
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    llm = ScriptedLLMBackend(
        {
            "classification": [
                {
                    "normalized_task": "Add feature with public docs impact",
                    "needs_world_facts": False,
                    "execution_family": ExecutionFamily.REPOSITORY_CHANGE.value,
                    "task_intent": "implement",
                    "capabilities": [Capability.REPO_READ.value, Capability.REPO_WRITE.value],
                    "observation_focus": [],
                    "reasoning": "Repository feature implementation.",
                    "risk_level": "medium",
                }
            ],
            "route_analysis": [
                {
                    "needs_repository_observation": True,
                    "needs_world_observation": False,
                    "needs_fresh_external_research": False,
                    "can_plan_immediately": True,
                    "required_evidence_types": [],
                    "research_targets": [],
                    "observation_focus": ["repository structure", "test/docs layout"],
                    "reasoning": "Repository observation is required before implementation.",
                }
            ],
            "obligation_analysis": [
                {
                    "required_test_levels": ["unit"],
                    "required_setup_steps": [],
                    "required_environment_conditions": [],
                    "completion_requirements": ["feature works"],
                    "blocker_conditions": [],
                    "reasoning_summary": "Initial feature obligations.",
                },
                {
                    "required_test_levels": ["unit"],
                    "required_setup_steps": [],
                    "required_environment_conditions": [],
                    "required_documentation_updates": ["README public usage section"],
                    "completion_requirements": ["feature works", "README public usage section updated"],
                    "blocker_conditions": [],
                    "reasoning_summary": "Rediscovery includes docs impact.",
                },
            ],
            "planning": [
                {
                    "summary": "Implement feature",
                    "execution_family": ExecutionFamily.REPOSITORY_CHANGE.value,
                    "task_intent": "implement",
                    "deliverable_kind": "repository_changes",
                    "capabilities": [Capability.REPO_WRITE.value],
                    "steps": ["edit src/app.py", "run unit tests"],
                    "success_criteria": ["feature works"],
                    "verification_checks": ["unit tests pass"],
                    "requires_mutation": True,
                    "must_change_world": True,
                    "expected_repo_changes": ["src/app.py"],
                    "required_test_levels": ["unit"],
                    "reasoning": "Initial plan omitted docs.",
                },
                {
                    "summary": "Implement feature and docs",
                    "execution_family": ExecutionFamily.REPOSITORY_CHANGE.value,
                    "task_intent": "implement",
                    "deliverable_kind": "repository_changes",
                    "capabilities": [Capability.REPO_WRITE.value],
                    "steps": ["edit src/app.py", "update README public usage section", "run unit tests"],
                    "success_criteria": ["feature works", "README public usage section updated"],
                    "verification_checks": ["unit tests pass", "README public usage section updated"],
                    "requires_mutation": True,
                    "must_change_world": True,
                    "expected_repo_changes": ["src/app.py", "README public usage section"],
                    "required_test_levels": ["unit"],
                    "reasoning": "Replanned with docs obligation.",
                },
            ],
            "verification": [
                {
                    "passed": False,
                    "summary": "Feature works, but documentation impact discovered: README public usage section is missing.",
                    "checks_passed": ["unit tests pass"],
                    "checks_failed": ["README public usage section updated"],
                    "missing_evidence": [],
                    "confidence": "high",
                    "reasoning": "Public surface needs docs.",
                    "performed_test_levels": ["unit"],
                    "missing_obligations": ["documentation impact discovered: README public usage section required"],
                    "completion_status": "partially_completed",
                },
                {
                    "passed": True,
                    "summary": "Feature and README public usage section are present with unit tests.",
                    "checks_passed": ["unit tests pass", "README public usage section updated"],
                    "checks_failed": [],
                    "missing_evidence": [],
                    "confidence": "high",
                    "reasoning": "All re-discovered obligations have evidence.",
                    "performed_test_levels": ["unit"],
                    "missing_obligations": [],
                    "completion_status": "completed",
                },
            ],
        }
    )
    openhands = FakeOpenHandsAdapter(
        artifact_store,
        scripts={
            "observe": ["Observed repo structure with src/app.py, README.md, and unit tests."],
            "execute": [
                "Changed src/app.py. Ran unit tests passed.",
                "Changed src/app.py and README public usage section. Ran unit tests passed.",
            ],
        },
    )
    controller = WorkflowController(
        llm_backend=llm,
        openhands_adapter=openhands,
        artifact_root=tmp_path / "artifacts",
        approval_provider=StaticApprovalProvider(approve=True, reviewer="test"),
    )

    report = await controller.run(Task(description="Add a public feature and keep the repo complete"))

    assert report.status == "completed"
    assert len(llm.calls["obligation_analysis"]) == 2
    assert len(llm.calls["planning"]) == 2
    assert len(openhands.calls["execute"]) == 2
    assert report.acceptance_contract is not None
    assert any(item.kind == AcceptanceObligationKind.DOCUMENTATION_UPDATED for item in report.acceptance_contract.obligations)
