from __future__ import annotations

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.control_plane import CheckpointStore, WorkflowCheckpoint
from artifact_workflow_runtime.control_plane.recovery import build_resume_decision
from artifact_workflow_runtime.models import ExecutionFamily, ExecutionPlan, FinalReport, Task
from artifact_workflow_runtime.models.state import WorkflowStateSnapshot, WorkflowStatus


def _planned_snapshot() -> WorkflowStateSnapshot:
    return WorkflowStateSnapshot(
        task=Task(id="task_resume", description="Implement feature"),
        status=WorkflowStatus.PLANNED,
        plan=ExecutionPlan(
            summary="plan",
            execution_family=ExecutionFamily.REPOSITORY_CHANGE,
            task_intent="implement",
            deliverable_kind="repository_changes",
            steps=["code"],
            success_criteria=["done"],
            verification_checks=["tests"],
            requires_mutation=True,
            must_change_world=True,
            reasoning="bounded plan",
        ),
    )


def test_build_resume_decision_for_nonterminal_checkpoint() -> None:
    checkpoint = WorkflowCheckpoint(
        checkpoint_id="chk_1",
        artifact_id="chk_1",
        stage="plan",
        task_id="task_resume",
        status=WorkflowStatus.PLANNED,
        state=_planned_snapshot(),
    )

    decision = build_resume_decision(checkpoint)

    assert decision.allowed is True
    assert decision.resume_from_stage == "policy"


def test_checkpoint_store_recovers_latest_checkpoint(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    snapshot = _planned_snapshot()
    artifact = store.add_json(
        "workflow_checkpoint",
        {
            "stage": "plan",
            "task_id": snapshot.task.id,
            "status": snapshot.status.value,
            "before_status": "done_contract_built",
            "update_keys": ["plan", "status"],
            "state": snapshot.model_dump(mode="json"),
            "validated": True,
        },
        metadata={"stage": "plan", "task_id": snapshot.task.id, "status": snapshot.status.value},
    )
    checkpoint_store = CheckpointStore(store)

    recovered = checkpoint_store.recover(task_id=snapshot.task.id)

    assert recovered.checkpoint.artifact_id == artifact.id
    assert recovered.resume.allowed is True
    assert recovered.resume.resume_from_stage == "policy"
    assert recovered.snapshot.status == WorkflowStatus.PLANNED


def test_terminal_checkpoint_cannot_resume() -> None:
    snapshot = WorkflowStateSnapshot(
        task=Task(id="task_terminal", description="Done"),
        status=WorkflowStatus.COMPLETED,
        final_report=FinalReport(task_id="task_terminal", status="completed", summary="done"),
    )
    checkpoint = WorkflowCheckpoint(
        checkpoint_id="chk_terminal",
        artifact_id="chk_terminal",
        stage="finalize",
        task_id="task_terminal",
        status=WorkflowStatus.COMPLETED,
        state=snapshot,
    )

    decision = build_resume_decision(checkpoint)

    assert decision.allowed is False
    assert decision.resume_from_stage is None
