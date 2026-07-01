from __future__ import annotations

from artifact_workflow_runtime.decomposition import DecompositionPlanner, ExecutionPacket, ExecutionPacketStatus, ExecutionPacketType, DecompositionPlan
from artifact_workflow_runtime.models import AcceptanceObligation, AcceptanceObligationKind, ExecutionFamily, ObligationAnalysis, Task, TaskAcceptanceContract, VerificationResult
from artifact_workflow_runtime.models.state import WorkflowStateSnapshot
from artifact_workflow_runtime.strategy import StrategyId


def _acceptance(*kinds: AcceptanceObligationKind) -> TaskAcceptanceContract:
    return TaskAcceptanceContract(
        task_id="task_1",
        execution_family=ExecutionFamily.REPOSITORY_CHANGE,
        obligations=[AcceptanceObligation(kind=kind, name=kind.value) for kind in kinds],
    )


def test_planner_derives_docs_and_integration_packets_from_runtime_facts() -> None:
    planner = DecompositionPlanner()
    snapshot = WorkflowStateSnapshot(
        task=Task(description="implement feature X"),
        verification_result=VerificationResult(
            request_id="verify_req",
            passed=False,
            summary="Missing integration and docs evidence.",
            evidence_text="verification",
            missing_evidence=["README update", "integration validation"],
            missing_obligations=["documentation update", "integration verification"],
            confidence="medium",
        ),
        obligations=ObligationAnalysis(reasoning_summary="discovered docs/integration obligations"),
    )
    plan = planner.build_plan(
        task=snapshot.task,
        strategy_id=StrategyId.DEFAULT,
        acceptance_contract=_acceptance(AcceptanceObligationKind.DOCUMENTATION_UPDATED, AcceptanceObligationKind.INTEGRATION_TESTS_RUN),
        obligations=snapshot.obligations,
        snapshot=snapshot,
    )
    packet_types = [packet.packet_type for packet in plan.packets]

    assert ExecutionPacketType.DOCS in packet_types
    assert ExecutionPacketType.INTEGRATION in packet_types


def test_planner_skips_redundant_implementation_when_already_completed() -> None:
    planner = DecompositionPlanner()
    completed_impl = ExecutionPacket(
        packet_id="pkt_impl",
        title="implementation",
        goal="implement feature",
        scope="bounded impl",
        packet_type=ExecutionPacketType.IMPLEMENTATION,
        status=ExecutionPacketStatus.COMPLETED,
        success_criteria=["impl exists"],
        required_evidence=["changed files"],
    )
    snapshot = WorkflowStateSnapshot(
        task=Task(description="implement feature X"),
        decomposition_plan=DecompositionPlan(
            plan_id="plan_existing",
            task_summary="implement feature X",
            strategy_id=StrategyId.DEFAULT.value,
            packets=[completed_impl],
            decomposition_reason="existing work",
        ),
        obligations=ObligationAnalysis(required_documentation_updates=["README"], reasoning_summary="docs still missing"),
    )
    plan = planner.build_plan(task=snapshot.task, strategy_id=StrategyId.DEFAULT, obligations=snapshot.obligations, snapshot=snapshot)

    assert all(packet.packet_type != ExecutionPacketType.IMPLEMENTATION for packet in plan.packets)
    assert any(packet.packet_type == ExecutionPacketType.DOCS for packet in plan.packets)
