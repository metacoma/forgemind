from __future__ import annotations

from artifact_workflow_runtime.control_plane.stage_filters import packet_scoped_execute_items
from artifact_workflow_runtime.decomposition import PacketSelector
from artifact_workflow_runtime.decomposition.models import (
    DecompositionComplexity,
    DecompositionPlan,
    ExecutionPacket,
    ExecutionPacketStatus,
    ExecutionPacketType,
    PacketLocalContract,
)
from artifact_workflow_runtime.models import AcceptanceObligation, AcceptanceObligationKind, ExecutionFamily, ObligationAnalysis, Task, TaskAcceptanceContract
from artifact_workflow_runtime.policy import RuntimeAction
from artifact_workflow_runtime.strategy import StrategyGovernor, StrategyId
from artifact_workflow_runtime.strategy.models import StrategyCheckpointSignals
from artifact_workflow_runtime.models.state import WorkflowStateSnapshot
from artifact_workflow_runtime.decomposition.planner import DecompositionPlanner


def test_runtime_action_registry_normalizes_freshness_actions() -> None:
    assert RuntimeAction.coerce("inspect_package_registry") == RuntimeAction.INTERNET_SEARCH
    assert RuntimeAction.coerce("inspect_release_notes") == RuntimeAction.INTERNET_SEARCH
    assert RuntimeAction.coerce("resolve_package_versions") == RuntimeAction.INTERNET_SEARCH


def test_packet_selector_prefers_setup_packet_by_priority_when_ready() -> None:
    plan = DecompositionPlan(
        plan_id="plan",
        task_summary="add runtime dependent feature",
        complexity=DecompositionComplexity.SMALL,
        decomposition_reason="test",
        packets=[
            ExecutionPacket(
                packet_id="impl",
                title="implementation",
                goal="goal",
                scope="scope",
                packet_type=ExecutionPacketType.IMPLEMENTATION,
                status=ExecutionPacketStatus.PENDING,
            ),
            ExecutionPacket(
                packet_id="setup",
                title="setup",
                goal="goal",
                scope="scope",
                packet_type=ExecutionPacketType.SETUP,
                status=ExecutionPacketStatus.PENDING,
            ),
        ],
    )

    selection = PacketSelector().select(plan=plan, active_strategy=StrategyId.DEFAULT)

    assert selection.ready is True
    assert selection.selected_packet_id == "setup"


def test_setup_packet_local_contract_is_environment_only() -> None:
    task = Task(description="Add C#/.NET gRPC client library")
    acceptance = TaskAcceptanceContract(
        task_id=task.id,
        execution_family=ExecutionFamily.REPOSITORY_CHANGE,
        obligations=[
            AcceptanceObligation(kind=AcceptanceObligationKind.ENVIRONMENT_PREREQUISITES_SATISFIED, name="env ready"),
            AcceptanceObligation(kind=AcceptanceObligationKind.CODE_CHANGED, name="code changed"),
        ],
        required_environment_prerequisites=[".NET SDK 8.0+ installed", "runtime_under_test"],
    )
    obligations = ObligationAnalysis(
        required_setup_steps=["Install .NET SDK 8.0+"],
        required_environment_conditions=["runtime_under_test"],
        affected_surfaces=["grpc/csharp", "README.md"],
        required_test_levels=["build", "unit", "integration"],
        reasoning_summary="controller discovered environment and verification obligations",
    )

    plan = DecompositionPlanner().build_plan(
        task=task,
        strategy_id=StrategyId.DEFAULT,
        acceptance_contract=acceptance,
        obligations=obligations,
        snapshot=WorkflowStateSnapshot(task=task, obligations=obligations),
    )

    setup_packet = next(packet for packet in plan.packets if packet.packet_type == ExecutionPacketType.SETUP)
    assert setup_packet.local_contract.environment_nodes
    assert not setup_packet.local_contract.work_surfaces
    assert setup_packet.target_areas == setup_packet.local_contract.environment_nodes
    assert setup_packet.allowed_files == []


def test_packet_scoped_execute_items_keeps_setup_steps_out_of_implementation_scope() -> None:
    packet = ExecutionPacket(
        packet_id="setup",
        title="setup runtime",
        goal="install SDK and probe tools",
        scope="bootstrap only",
        packet_type=ExecutionPacketType.SETUP,
        local_contract=PacketLocalContract(environment_nodes=[".NET SDK", "runtime_under_test"]),
    )
    scoped = packet_scoped_execute_items(
        [
            "Install .NET SDK and run dotnet --version",
            "Implement the full C# gRPC client library",
            "Update README examples",
            "Add GitHub Actions workflow",
        ],
        packet,
    )

    assert scoped == ["Install .NET SDK and run dotnet --version"]


def test_strategy_governor_prefers_repair_only_for_explicit_build_test_failure() -> None:
    snapshot = WorkflowStateSnapshot(task=Task(description="add C# client library"))
    signals = StrategyCheckpointSignals(
        current_stage="repair",
        explicit_failure_class="build_test_failure",
        failed_check_levels=["build", "unit"],
        active_packet_type="test",
        missing_evidence=["missing_evidence"],
        has_tests_obligations=True,
    )

    decision = StrategyGovernor().decide(snapshot=snapshot, signals=signals)

    assert decision.selected_strategy == StrategyId.REPAIR_ONLY
    assert "explicit_failure_class" in decision.signals_used
