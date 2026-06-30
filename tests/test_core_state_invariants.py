from __future__ import annotations

import pytest

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.graph.stage_gates import StageReadinessError, StageReadinessGate
from artifact_workflow_runtime.models import Artifact, ExecutionFamily, FinalReport, Task
from artifact_workflow_runtime.models.state import (
    CoreWorkflowStage,
    WorkflowStateSnapshot,
    WorkflowStatus,
    produced_fields_for_stage,
    required_fields_for_stage,
    validate_workflow_state,
)
from artifact_workflow_runtime.state import WorkflowCheckpointError, WorkflowCheckpointRecorder


def test_canonical_stage_contracts_define_execute_and_publish_boundaries() -> None:
    assert required_fields_for_stage(CoreWorkflowStage.EXECUTE) == ("task", "plan", "context_packet")
    assert "execution_result" in produced_fields_for_stage(CoreWorkflowStage.EXECUTE)
    assert required_fields_for_stage(CoreWorkflowStage.PUBLISH) == (
        "task",
        "plan",
        "execution_result",
        "done_contract",
        "acceptance_decision",
    )
    assert "publish_result" in produced_fields_for_stage(CoreWorkflowStage.PUBLISH)


def test_stage_readiness_gate_uses_canonical_contract_when_fields_not_overridden() -> None:
    gate = StageReadinessGate()
    state = {"task": {"id": "task_1", "description": "x"}, "status": "created"}

    with pytest.raises(StageReadinessError, match="plan"):
        gate.require(state, "execute")

    gate.require(state, "classify")


def test_workflow_snapshot_invariants_catch_transition_artifact_drift() -> None:
    task = Task(description="x")
    snapshot = WorkflowStateSnapshot(
        task=task,
        task_artifact=Artifact(kind="task", path="task.json", media_type="application/json"),
    ).with_transition(
        stage="intake",
        to_status=WorkflowStatus.INTAKE_COMPLETED,
        reason="persisted",
        artifact_ids_added=["artifact_task"],
    )
    valid = validate_workflow_state(snapshot.to_graph_state())
    assert valid.status == WorkflowStatus.INTAKE_COMPLETED

    broken = valid.model_copy(update={"artifact_ids": []})
    assert "missing from artifact_ids" in "; ".join(broken.core_invariant_errors())


def test_final_report_status_can_use_specific_failure_status_that_coerces_to_failed() -> None:
    task = Task(description="x")
    report = FinalReport(task_id=task.id, status="agent_failed", summary="OpenHands failed")
    snapshot = WorkflowStateSnapshot(task=task, final_report=report).with_transition(
        stage="finalize",
        to_status=WorkflowStatus.FAILED,
        reason="agent failed",
    )

    assert snapshot.core_invariant_errors(final=True) == []


def test_checkpoint_recorder_rejects_invalid_typed_update(tmp_path) -> None:
    recorder = WorkflowCheckpointRecorder(ArtifactStore(tmp_path))

    with pytest.raises(WorkflowCheckpointError, match="invalid workflow state"):
        recorder.record(
            stage="classify",
            before={"task": {"id": "task_1", "description": "x"}, "status": "created", "artifact_ids": []},
            update={"status": "classified", "classification": {"ok": True}},
        )
