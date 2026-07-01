from __future__ import annotations

import pytest
from pydantic import ValidationError

from artifact_workflow_runtime.decomposition import (
    DecompositionComplexity,
    DecompositionPlan,
    DecompositionPlanner,
    DecompositionProgressDecision,
    DecompositionValidator,
    ExecutionPacket,
    ExecutionPacketStatus,
    ExecutionPacketType,
    PacketSelector,
)
from artifact_workflow_runtime.models import (
    AcceptanceObligation,
    AcceptanceObligationKind,
    ExecutionFamily,
    ObligationAnalysis,
    Task,
    TaskAcceptanceContract,
)
from artifact_workflow_runtime.strategy import StrategyId
from artifact_workflow_runtime.models.state import WorkflowStateSnapshot


def _acceptance(*kinds: AcceptanceObligationKind) -> TaskAcceptanceContract:
    return TaskAcceptanceContract(
        task_id="task_1",
        execution_family=ExecutionFamily.REPOSITORY_CHANGE,
        obligations=[AcceptanceObligation(kind=kind, name=kind.value) for kind in kinds],
    )


def test_execution_packet_serializes_and_deserializes() -> None:
    packet = ExecutionPacket(
        packet_id="pkt_1",
        title="implement minimal slice",
        goal="implement feature x",
        scope="Change only the minimal happy path.",
        packet_type=ExecutionPacketType.IMPLEMENTATION,
        strategy_id=StrategyId.MVP_FIRST.value,
        success_criteria=["minimal slice works"],
        required_evidence=["changed files", "unit tests"],
    )

    payload = packet.model_dump(mode="json")
    restored = ExecutionPacket.model_validate(payload)

    assert restored.packet_id == "pkt_1"
    assert restored.packet_type == ExecutionPacketType.IMPLEMENTATION
    assert restored.status == ExecutionPacketStatus.PENDING


def test_unknown_packet_status_or_type_rejected() -> None:
    with pytest.raises(ValidationError):
        ExecutionPacket(
            packet_id="pkt_1",
            title="bad",
            goal="bad",
            scope="bad",
            packet_type="magic",
            success_criteria=["x"],
            required_evidence=["y"],
        )

    with pytest.raises(ValidationError):
        ExecutionPacket.model_validate(
            {
                "packet_id": "pkt_1",
                "title": "bad",
                "goal": "bad",
                "scope": "bad",
                "packet_type": "implementation",
                "status": "magic",
                "success_criteria": ["x"],
                "required_evidence": ["y"],
            }
        )


def test_decomposition_plan_stores_packets_and_strategy_id() -> None:
    packet = ExecutionPacket(
        packet_id="pkt_1",
        title="implement",
        goal="implement feature",
        scope="bounded",
        packet_type=ExecutionPacketType.IMPLEMENTATION,
        strategy_id=StrategyId.DEFAULT.value,
        success_criteria=["done"],
        required_evidence=["changed files"],
    )
    plan = DecompositionPlan(
        plan_id="plan_1",
        task_summary="implement feature",
        strategy_id=StrategyId.DEFAULT.value,
        complexity=DecompositionComplexity.SMALL,
        packets=[packet],
        decomposition_reason="default strategy",
    )

    assert plan.strategy_id == StrategyId.DEFAULT.value
    assert plan.packets[0].packet_id == "pkt_1"


def test_strategy_aware_packetization_shapes_packets() -> None:
    planner = DecompositionPlanner()
    task = Task(description="implement feature X in repo Y")

    bdd_plan = planner.build_plan(
        task=task,
        strategy_id=StrategyId.BDD_INCREMENTAL,
        acceptance_contract=_acceptance(AcceptanceObligationKind.RELEVANT_TESTS_RUN),
    )
    assert bdd_plan.packets[0].packet_type == ExecutionPacketType.TEST
    assert bdd_plan.packets[1].packet_type == ExecutionPacketType.IMPLEMENTATION

    spike_plan = planner.build_plan(task=task, strategy_id=StrategyId.SPIKE_THEN_HARDEN)
    assert spike_plan.packets[0].packet_type == ExecutionPacketType.SPIKE

    repair_plan = planner.build_plan(task=task, strategy_id=StrategyId.REPAIR_ONLY)
    assert {packet.packet_type for packet in repair_plan.packets} <= {ExecutionPacketType.REPAIR, ExecutionPacketType.VERIFICATION}

    refactor_plan = planner.build_plan(task=task, strategy_id=StrategyId.SAFE_REFACTOR)
    assert refactor_plan.packets[0].packet_type == ExecutionPacketType.REFACTOR

    mvp_plan = planner.build_plan(
        task=task,
        strategy_id=StrategyId.MVP_FIRST,
        acceptance_contract=_acceptance(AcceptanceObligationKind.RELEVANT_TESTS_RUN, AcceptanceObligationKind.DOCUMENTATION_UPDATED),
    )
    assert mvp_plan.packets[0].title == "minimal working slice"


def test_packet_selector_respects_dependencies_and_repair_only() -> None:
    selector = PacketSelector()
    packets = [
        ExecutionPacket(packet_id="p1", title="tests", goal="task", scope="tests", packet_type=ExecutionPacketType.TEST, success_criteria=["tests"], required_evidence=["tests"], status=ExecutionPacketStatus.PENDING),
        ExecutionPacket(packet_id="p2", title="impl", goal="task", scope="impl", packet_type=ExecutionPacketType.IMPLEMENTATION, dependencies=["p1"], success_criteria=["impl"], required_evidence=["changed files"], status=ExecutionPacketStatus.PENDING),
    ]
    plan = DecompositionPlan(plan_id="plan", task_summary="task", strategy_id=StrategyId.BDD_INCREMENTAL.value, complexity=DecompositionComplexity.MEDIUM, packets=packets, decomposition_reason="bdd")

    selected = selector.select(plan=plan, active_strategy=StrategyId.BDD_INCREMENTAL)
    assert selected.selected_packet_id == "p1"
    assert selected.ready is True

    blocked_plan = plan.model_copy(update={"packets": [packets[0].model_copy(update={"status": ExecutionPacketStatus.COMPLETED}), packets[1].model_copy(update={"status": ExecutionPacketStatus.PENDING, "dependencies": ["missing"]})]})
    blocked = selector.select(plan=blocked_plan, active_strategy=StrategyId.DEFAULT)
    assert blocked.ready is False
    assert blocked.blocked_reason == "dependencies_not_ready"

    repair_packets = [
        ExecutionPacket(packet_id="p1", title="failed impl", goal="task", scope="impl", packet_type=ExecutionPacketType.IMPLEMENTATION, success_criteria=["impl"], required_evidence=["changed files"], status=ExecutionPacketStatus.FAILED),
    ]
    repair_plan = DecompositionPlan(plan_id="plan_r", task_summary="task", strategy_id=StrategyId.REPAIR_ONLY.value, complexity=DecompositionComplexity.SMALL, packets=repair_packets, decomposition_reason="repair")
    repair_selected = selector.select(plan=repair_plan, active_strategy=StrategyId.REPAIR_ONLY)
    assert repair_selected.selected_packet_id == "p1"
    assert repair_selected.ready is True


def test_decomposition_validator_rejects_invalid_and_falls_back() -> None:
    validator = DecompositionValidator()
    packets = [
        ExecutionPacket(packet_id="dup", title="one", goal="task", scope="one", packet_type=ExecutionPacketType.IMPLEMENTATION, success_criteria=["done"], required_evidence=["changed"]),
        ExecutionPacket(packet_id="dup", title="two", goal="task", scope="two", packet_type=ExecutionPacketType.TEST, dependencies=["missing"], success_criteria=["tests"], required_evidence=["tests"]),
    ]
    plan = DecompositionPlan(plan_id="plan", task_summary="task", strategy_id=StrategyId.DEFAULT.value, complexity=DecompositionComplexity.SMALL, packets=packets, decomposition_reason="invalid")
    result = validator.validate(plan)

    assert result.valid is False
    assert result.fallback_used is True
    assert result.normalized_plan is not None
    assert len(result.normalized_plan.packets) == 1


def test_decomposition_validator_detects_cycle() -> None:
    validator = DecompositionValidator()
    packets = [
        ExecutionPacket(packet_id="p1", title="one", goal="task", scope="one", packet_type=ExecutionPacketType.IMPLEMENTATION, dependencies=["p2"], success_criteria=["done"], required_evidence=["changed"]),
        ExecutionPacket(packet_id="p2", title="two", goal="task", scope="two", packet_type=ExecutionPacketType.TEST, dependencies=["p1"], success_criteria=["tests"], required_evidence=["tests"]),
    ]
    plan = DecompositionPlan(plan_id="plan", task_summary="task", strategy_id=StrategyId.DEFAULT.value, complexity=DecompositionComplexity.SMALL, packets=packets, decomposition_reason="cycle")
    result = validator.validate(plan)

    assert result.valid is False
    assert any("cycle" in issue for issue in result.issues)


def test_decomposition_progress_decision_serializes() -> None:
    decision = DecompositionProgressDecision(
        current_packet_id="p1",
        selected_next_packet_id="p2",
        selected_next_stage="execute",
        reason="next pending packet is runnable",
    )
    restored = DecompositionProgressDecision.model_validate(decision.model_dump(mode="json"))
    assert restored.selected_next_stage == "execute"
    assert restored.selected_next_packet_id == "p2"


def test_planner_uses_runtime_facts_from_snapshot() -> None:
    planner = DecompositionPlanner()
    task = Task(description="implement feature X in repo Y")
    snapshot = WorkflowStateSnapshot(
        task=task,
        obligations=ObligationAnalysis(
            required_test_levels=["unit"],
            required_documentation_updates=["README"],
            affected_surfaces=["src/app.py"],
            blocker_conditions=["freeplane runtime not installed"],
            reasoning_summary="tests and docs required",
        ),
        packet_history=[],
    )
    plan = planner.build_plan(task=task, strategy_id=StrategyId.MVP_FIRST, obligations=snapshot.obligations, snapshot=snapshot)

    assert any("freeplane runtime not installed" in risk for risk in plan.risks)
    assert any(packet.target_areas for packet in plan.packets)
    assert any(packet.allowed_files for packet in plan.packets)


def test_decomposition_validator_rejects_self_dependency_and_missing_scope() -> None:
    validator = DecompositionValidator()
    packet = ExecutionPacket.model_validate({
        "packet_id": "p1",
        "title": "one",
        "goal": "task",
        "scope": "one",
        "packet_type": "implementation",
        "dependencies": ["p1"],
        "success_criteria": ["done"],
        "required_evidence": ["changed"],
    })
    plan = DecompositionPlan(plan_id="plan_self", task_summary="task", strategy_id=StrategyId.DEFAULT.value, complexity=DecompositionComplexity.SMALL, packets=[packet], decomposition_reason="invalid")
    result = validator.validate(plan, fallback_to_single_packet=False)

    assert result.valid is False
    assert any("depends on itself" in issue for issue in result.issues)
