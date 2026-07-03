from __future__ import annotations

from types import SimpleNamespace

import pytest

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.control_plane import RuntimeKernel
from artifact_workflow_runtime.models import BlockerEvidence, ExecutionResult, Task
from artifact_workflow_runtime.models.state import WorkflowStateSnapshot
from artifact_workflow_runtime.strategy.runtime import record_strategy_checkpoint
from artifact_workflow_runtime.strategy import StrategyCatalog, StrategyGovernor
from artifact_workflow_runtime.strategy.models import StrategyCheckpointSignals, StrategyDecision, StrategyId


def test_strategy_catalog_contains_minimal_strategy_set() -> None:
    catalog = StrategyCatalog()

    assert {item.id for item in catalog.list()} == set(StrategyId)
    for strategy_id in StrategyId:
        definition = catalog.get(strategy_id)
        assert definition.description
        assert definition.applicable_when
        assert isinstance(definition.packet_stage_preferences, dict)
        assert definition.verification_expectations


def test_strategy_governor_selects_repair_only_for_failed_execution() -> None:
    task = Task(description="fix failing CI")
    snapshot = WorkflowStateSnapshot(task=task)
    signals = StrategyCheckpointSignals(current_stage="execution_review", execution_status="failed")

    decision = StrategyGovernor().decide(snapshot=snapshot, signals=signals)

    assert decision.selected_strategy == StrategyId.REPAIR_ONLY
    assert "execution_status" in decision.signals_used
    assert decision.checkpoint_stage == "execution_review"


def test_strategy_governor_selects_bdd_incremental_for_missing_test_evidence() -> None:
    task = Task(description="add behavior for parser")
    snapshot = WorkflowStateSnapshot(task=task)
    signals = StrategyCheckpointSignals(
        current_stage="verify",
        missing_evidence=["missing behavior regression test"],
        has_tests_obligations=True,
    )

    decision = StrategyGovernor().decide(snapshot=snapshot, signals=signals)

    assert decision.selected_strategy == StrategyId.BDD_INCREMENTAL
    assert "missing_evidence" in decision.signals_used


def test_strategy_decision_serializes_in_workflow_snapshot() -> None:
    task = Task(description="stabilize runtime")
    decision = StrategyDecision(
        selected_strategy=StrategyId.SAFE_REFACTOR,
        previous_strategy=StrategyId.DEFAULT,
        reason="Refactor-like stabilization request.",
        checkpoint_stage="plan",
        signals_used=["task_description"],
    )

    snapshot = WorkflowStateSnapshot(
        task=task,
        active_strategy=StrategyId.SAFE_REFACTOR,
        strategy_decisions=[decision],
    )
    restored = WorkflowStateSnapshot.from_graph_state(snapshot.to_graph_state())

    assert restored.active_strategy == StrategyId.SAFE_REFACTOR
    assert restored.strategy_decisions[0].selected_strategy == StrategyId.SAFE_REFACTOR
    assert restored.strategy_decisions[0].previous_strategy == StrategyId.DEFAULT


def test_runtime_records_strategy_decision_artifact(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    services = SimpleNamespace(
        artifact_store=store,
        strategy_governor=StrategyGovernor(),
        runtime_kernel=RuntimeKernel(),
    )
    task = Task(description="implement small feature")
    state = WorkflowStateSnapshot(task=task).to_graph_state()

    update = record_strategy_checkpoint(services, state, checkpoint_stage="plan")

    assert update["active_strategy"] in {item.value for item in StrategyId}
    assert len(update["strategy_decisions"]) == 1
    artifact_ids = update["artifact_ids"]
    assert artifact_ids
    artifact = store.get(artifact_ids[-1])
    assert artifact.kind == "strategy_decision"
    payload = store.read_json(artifact.id)
    assert payload["decision"]["selected_strategy"] == update["active_strategy"]
    assert payload["signals"]["current_stage"] == "plan"


def test_strategy_governor_rejects_unknown_strategy(monkeypatch: pytest.MonkeyPatch) -> None:
    governor = StrategyGovernor()
    task = Task(description="x")
    snapshot = WorkflowStateSnapshot(task=task)
    signals = StrategyCheckpointSignals(current_stage="plan")

    monkeypatch.setattr(
        governor,
        "_select",
        lambda *, snapshot, signals: ("not_registered", "bad strategy", "high", ["test"]),
    )

    with pytest.raises(ValueError, match="unknown strategy"):
        governor.decide(snapshot=snapshot, signals=signals)


def test_strategy_governor_prefers_spike_for_environment_blockers() -> None:
    task = Task(description="add client using unfamiliar runtime API")
    execution = ExecutionResult(
        request_id="exec_1",
        ok=True,
        summary="blocked by SDK install",
        evidence_text="environment blocker",
    )
    execution.structured_evidence.blockers.append(
        BlockerEvidence(summary="Unknown SDK installation path blocks integration test", severity="medium")
    )
    snapshot = WorkflowStateSnapshot(task=task, execution_result=execution)
    signals = StrategyCheckpointSignals(
        current_stage="review",
        blockers=["Unknown SDK installation path blocks integration test"],
    )

    decision = StrategyGovernor().decide(snapshot=snapshot, signals=signals)

    assert decision.selected_strategy == StrategyId.SPIKE_THEN_HARDEN
