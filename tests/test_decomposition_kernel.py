from __future__ import annotations

from artifact_workflow_runtime.control_plane import RuntimeKernel
from artifact_workflow_runtime.decomposition import (
    DecompositionComplexity,
    DecompositionOutcome,
    DecompositionPlan,
    ExecutionPacket,
    ExecutionPacketStatus,
    ExecutionPacketType,
)
from artifact_workflow_runtime.reports import FinalReportBuilder
from artifact_workflow_runtime.models import Task
from artifact_workflow_runtime.strategy import StrategyId


def _packet(packet_id: str, status: ExecutionPacketStatus, *, dependencies: list[str] | None = None) -> ExecutionPacket:
    return ExecutionPacket(
        packet_id=packet_id,
        title=packet_id,
        goal="implement feature x",
        scope="bounded",
        packet_type=ExecutionPacketType.IMPLEMENTATION,
        status=status,
        strategy_id=StrategyId.DEFAULT.value,
        dependencies=dependencies or [],
        success_criteria=["done"],
        required_evidence=["changed files"],
    )


def test_kernel_exposes_explicit_decomposition_outcomes() -> None:
    kernel = RuntimeKernel()

    completed_plan = DecompositionPlan(
        plan_id="plan_complete",
        task_summary="feature x",
        strategy_id=StrategyId.DEFAULT.value,
        complexity=DecompositionComplexity.SMALL,
        packets=[_packet("p1", ExecutionPacketStatus.COMPLETED)],
        decomposition_reason="done",
    )
    completed = kernel.evaluate_decomposition_progression(
        decomposition_plan=completed_plan,
        active_strategy=StrategyId.DEFAULT.value,
        current_packet_id="p1",
    )
    assert completed is not None
    assert completed.outcome == DecompositionOutcome.DECOMPOSITION_COMPLETED
    assert kernel.next_stage_after_decomposition_progression(completed) == "qa_plan"
    assert kernel.decomposition_terminal_status(completed) is None

    blocked_plan = DecompositionPlan(
        plan_id="plan_blocked",
        task_summary="feature x",
        strategy_id=StrategyId.DEFAULT.value,
        complexity=DecompositionComplexity.SMALL,
        packets=[
            _packet("p1", ExecutionPacketStatus.COMPLETED),
            _packet("p2", ExecutionPacketStatus.PENDING, dependencies=["missing_packet"]),
        ],
        decomposition_reason="blocked",
    )
    blocked = kernel.evaluate_decomposition_progression(
        decomposition_plan=blocked_plan,
        active_strategy=StrategyId.DEFAULT.value,
        current_packet_id="p1",
    )
    assert blocked is not None
    assert blocked.outcome == DecompositionOutcome.MANUAL_INTERVENTION_REQUIRED
    assert blocked.terminal is True
    assert kernel.next_stage_after_decomposition_progression(blocked) == "finalize"
    assert kernel.decomposition_terminal_status(blocked) == "blocked"

    failed_plan = DecompositionPlan(
        plan_id="plan_failed",
        task_summary="feature x",
        strategy_id=StrategyId.DEFAULT.value,
        complexity=DecompositionComplexity.SMALL,
        packets=[_packet("p1", ExecutionPacketStatus.FAILED)],
        decomposition_reason="failed",
    )
    failed = kernel.evaluate_decomposition_progression(
        decomposition_plan=failed_plan,
        active_strategy=StrategyId.DEFAULT.value,
        current_packet_id="p1",
    )
    assert failed is not None
    assert failed.outcome == DecompositionOutcome.FAILED_TERMINAL
    assert failed.terminal is True
    assert kernel.decomposition_terminal_status(failed) == "failed"


def test_finalizer_prefers_explicit_packet_terminal_outcome() -> None:
    builder = FinalReportBuilder()
    task = Task(description="implement feature x in repo y")
    incomplete_plan = DecompositionPlan(
        plan_id="plan_incomplete",
        task_summary=task.description,
        strategy_id=StrategyId.DEFAULT.value,
        complexity=DecompositionComplexity.SMALL,
        packets=[_packet("p1", ExecutionPacketStatus.COMPLETED), _packet("p2", ExecutionPacketStatus.PENDING, dependencies=["missing"] )],
        decomposition_reason="blocked",
    )
    decision = RuntimeKernel().evaluate_decomposition_progression(
        decomposition_plan=incomplete_plan,
        active_strategy=StrategyId.DEFAULT.value,
        current_packet_id="p1",
    )
    assert decision is not None and decision.terminal is True

    report = builder.build(
        task=task,
        classification=None,
        route=None,
        obligations=None,
        plan=None,
        policy=None,
        approval=None,
        research=None,
        observation=None,
        execution=None,
        publish=None,
        repair_results=[],
        verification=None,
        acceptance_contract=None,
        acceptance_decision=None,
        decomposition_plan=incomplete_plan,
        packet_progression=decision,
        artifact_ids=[],
    )

    assert report.status == "blocked"
    assert "pending packets" in report.summary or "blocked" in report.summary.lower()
