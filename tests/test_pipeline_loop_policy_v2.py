from __future__ import annotations

from artifact_workflow_runtime.control_plane import PipelineLoopPolicy
from artifact_workflow_runtime.lifecycle import LoopTerminalOutcome, PipelineLoopBudget, PipelineLoopTriggerKind, PipelineReentryTarget
from artifact_workflow_runtime.models import DiscoveredImpact, DiscoveredImpactKind, ExecutionFamily, ExecutionPlan, ObligationAnalysis


def _plan() -> ExecutionPlan:
    return ExecutionPlan(
        summary="Implement feature",
        execution_family=ExecutionFamily.REPOSITORY_CHANGE,
        task_intent="implement",
        deliverable_kind="repository_changes",
        steps=["change code"],
        success_criteria=["feature exists"],
        verification_checks=["tests pass"],
        requires_mutation=True,
        must_change_world=True,
        reasoning="bounded plan",
    )


def test_loop_policy_reenters_for_typed_docs_impact() -> None:
    policy = PipelineLoopPolicy()
    obligations = ObligationAnalysis(
        reasoning_summary="Docs discovered",
        discovered_impacts=[
            DiscoveredImpact(kind=DiscoveredImpactKind.DOCUMENTATION, summary="README update required", affected_paths=["README.md"], blocking=True)
        ],
    )

    decision = policy.evaluate(source_stage="verify", plan=_plan(), obligations=obligations)

    assert decision.allowed is True
    assert decision.automatic is True
    assert decision.trigger_kind == PipelineLoopTriggerKind.DOCS_IMPACT_DISCOVERED
    assert decision.target_stage == PipelineReentryTarget.OBLIGATIONS
    assert decision.trigger is not None
    assert "documentation" in decision.trigger.discovered_impacts


def test_loop_policy_reenters_for_typed_setup_impact() -> None:
    policy = PipelineLoopPolicy()
    obligations = ObligationAnalysis(
        reasoning_summary="Setup discovered",
        discovered_impacts=[
            DiscoveredImpact(kind=DiscoveredImpactKind.SETUP, summary="Docker image missing prerequisite", blocking=True)
        ],
    )

    decision = policy.evaluate(source_stage="verify", plan=_plan(), obligations=obligations)

    assert decision.allowed is True
    assert decision.trigger_kind == PipelineLoopTriggerKind.SETUP_GAP_DISCOVERED
    assert decision.target_stage == PipelineReentryTarget.OBSERVE


def test_loop_policy_budget_exhaustion_is_terminal() -> None:
    policy = PipelineLoopPolicy()
    obligations = ObligationAnalysis(
        reasoning_summary="Docs discovered",
        discovered_impacts=[
            DiscoveredImpact(kind=DiscoveredImpactKind.DOCUMENTATION, summary="README update required", blocking=True)
        ],
    )
    first = policy.evaluate(source_stage="verify", plan=_plan(), obligations=obligations)
    second = policy.evaluate(
        source_stage="verify",
        plan=_plan(),
        obligations=obligations,
        loop_decisions=[first],
        budget=PipelineLoopBudget(global_limit=3, per_trigger_limit=1, per_source_stage_limit=2, per_target_limit=2),
    )

    assert first.target_stage == PipelineReentryTarget.OBLIGATIONS
    assert second.allowed is False
    assert second.target_stage == PipelineReentryTarget.FINALIZE
    assert second.terminal_outcome == LoopTerminalOutcome.LOOP_EXHAUSTED
