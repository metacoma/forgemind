from __future__ import annotations

from typing import Any

from pydantic import Field

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.models import RuntimeModel
from artifact_workflow_runtime.models.state import TERMINAL_WORKFLOW_STATUSES, WorkflowStateSnapshot, WorkflowStatus


class WorkflowCheckpoint(RuntimeModel):
    checkpoint_id: str
    artifact_id: str
    stage: str
    task_id: str
    status: WorkflowStatus
    before_status: str | None = None
    update_keys: list[str] = Field(default_factory=list)
    state: WorkflowStateSnapshot
    validated: bool = True
    created_at: str | None = None


class ReplaySnapshot(RuntimeModel):
    checkpoint_id: str
    artifact_id: str
    stage: str
    status: WorkflowStatus
    next_stage: str | None = None
    created_at: str | None = None


class ResumeDecision(RuntimeModel):
    allowed: bool
    reason: str
    checkpoint_id: str | None = None
    artifact_id: str | None = None
    resume_from_stage: str | None = None
    last_safe_stage: str | None = None
    task_id: str | None = None


class RecoveredRuntimeState(RuntimeModel):
    checkpoint: WorkflowCheckpoint
    replay: ReplaySnapshot
    resume: ResumeDecision
    snapshot: WorkflowStateSnapshot


class CheckpointStore:
    def __init__(self, artifact_store: ArtifactStore) -> None:
        self.artifact_store = artifact_store

    def list_checkpoints(self, *, task_id: str | None = None) -> list[WorkflowCheckpoint]:
        checkpoints: list[WorkflowCheckpoint] = []
        for artifact in self.artifact_store.list():
            if artifact.kind != "workflow_checkpoint":
                continue
            payload = self.artifact_store.read_json(artifact.id)
            if task_id is not None and str(payload.get("task_id") or "") != task_id:
                continue
            state = WorkflowStateSnapshot.model_validate(payload["state"])
            checkpoints.append(
                WorkflowCheckpoint(
                    checkpoint_id=artifact.id,
                    artifact_id=artifact.id,
                    stage=str(payload.get("stage") or ""),
                    task_id=str(payload.get("task_id") or state.task.id),
                    status=WorkflowStatus.coerce(payload.get("status") or state.status),
                    before_status=payload.get("before_status"),
                    update_keys=[str(item) for item in payload.get("update_keys", [])],
                    state=state,
                    validated=bool(payload.get("validated", True)),
                    created_at=artifact.created_at,
                )
            )
        checkpoints.sort(key=lambda item: item.created_at or "")
        return checkpoints

    def latest_checkpoint(self, *, task_id: str) -> WorkflowCheckpoint | None:
        checkpoints = self.list_checkpoints(task_id=task_id)
        return checkpoints[-1] if checkpoints else None

    def replay(self, *, task_id: str) -> list[ReplaySnapshot]:
        snapshots: list[ReplaySnapshot] = []
        for checkpoint in self.list_checkpoints(task_id=task_id):
            snapshots.append(
                ReplaySnapshot(
                    checkpoint_id=checkpoint.checkpoint_id,
                    artifact_id=checkpoint.artifact_id,
                    stage=checkpoint.stage,
                    status=checkpoint.status,
                    next_stage=_derive_resume_stage(checkpoint.state),
                    created_at=checkpoint.created_at,
                )
            )
        return snapshots

    def recover(self, *, task_id: str) -> RecoveredRuntimeState:
        checkpoint = self.latest_checkpoint(task_id=task_id)
        if checkpoint is None:
            raise LookupError(f"No workflow checkpoint found for task {task_id!r}")
        resume = build_resume_decision(checkpoint)
        replay = ReplaySnapshot(
            checkpoint_id=checkpoint.checkpoint_id,
            artifact_id=checkpoint.artifact_id,
            stage=checkpoint.stage,
            status=checkpoint.status,
            next_stage=resume.resume_from_stage,
            created_at=checkpoint.created_at,
        )
        return RecoveredRuntimeState(checkpoint=checkpoint, replay=replay, resume=resume, snapshot=checkpoint.state)


def build_resume_decision(checkpoint: WorkflowCheckpoint) -> ResumeDecision:
    snapshot = checkpoint.state
    if snapshot.final_report is not None or snapshot.status in TERMINAL_WORKFLOW_STATUSES:
        return ResumeDecision(
            allowed=False,
            reason="Workflow is already terminal; resume is not allowed.",
            checkpoint_id=checkpoint.checkpoint_id,
            artifact_id=checkpoint.artifact_id,
            task_id=checkpoint.task_id,
            last_safe_stage=checkpoint.stage,
        )
    next_stage = _derive_resume_stage(snapshot)
    if next_stage is None:
        return ResumeDecision(
            allowed=False,
            reason="No safe resume stage could be derived from checkpoint semantics.",
            checkpoint_id=checkpoint.checkpoint_id,
            artifact_id=checkpoint.artifact_id,
            task_id=checkpoint.task_id,
            last_safe_stage=checkpoint.stage,
        )
    return ResumeDecision(
        allowed=True,
        reason=f"Recovered checkpoint is safe to resume from {next_stage}.",
        checkpoint_id=checkpoint.checkpoint_id,
        artifact_id=checkpoint.artifact_id,
        resume_from_stage=next_stage,
        task_id=checkpoint.task_id,
        last_safe_stage=checkpoint.stage,
    )


def _derive_resume_stage(snapshot: WorkflowStateSnapshot) -> str | None:
    if snapshot.controller_decisions:
        return snapshot.controller_decisions[-1].selected_next_stage
    mapping = {
        WorkflowStatus.CREATED: "intake",
        WorkflowStatus.INTAKE_COMPLETED: "classify",
        WorkflowStatus.CLASSIFIED: "route",
        WorkflowStatus.ROUTED: "research" if snapshot.route_decision and snapshot.route_decision.needs_fresh_external_research else ("observe" if snapshot.route_decision and (snapshot.route_decision.needs_repository_observation or snapshot.route_decision.needs_world_observation) else "build_context"),
        WorkflowStatus.RESEARCHED: "observe" if snapshot.route_decision and (snapshot.route_decision.needs_repository_observation or snapshot.route_decision.needs_world_observation) else "build_context",
        WorkflowStatus.OBSERVED: "build_context",
        WorkflowStatus.CONTEXT_BUILT: "obligations",
        WorkflowStatus.OBLIGATIONS_SYNTHESIZED: "done_contract",
        WorkflowStatus.DONE_CONTRACT_BUILT: "plan",
        WorkflowStatus.PLANNED: "policy",
        WorkflowStatus.POLICY_CHECKED: "approval" if snapshot.policy_decision and snapshot.policy_decision.requires_approval else ("workspace_prepare" if snapshot.policy_decision and not snapshot.policy_decision.blocked else "finalize"),
        WorkflowStatus.APPROVAL_RESOLVED: "workspace_prepare" if snapshot.approval_request and snapshot.approval_request.approved else "finalize",
        WorkflowStatus.WORKSPACE_PREPARED: "execute",
        WorkflowStatus.EXECUTED: "review",
        WorkflowStatus.REVIEWED: snapshot.controller_decisions[-1].selected_next_stage if snapshot.controller_decisions else "qa_plan",
        WorkflowStatus.QA_PLANNED: "qa_execute",
        WorkflowStatus.QA_EXECUTED: "qa_review",
        WorkflowStatus.QA_REVIEWED: snapshot.controller_decisions[-1].selected_next_stage if snapshot.controller_decisions else "acceptance",
        WorkflowStatus.REPAIRED: "review",
        WorkflowStatus.PUBLISH_REVIEWED: snapshot.controller_decisions[-1].selected_next_stage if snapshot.controller_decisions else "verify",
        WorkflowStatus.PUBLISHED: "post_publish_verify",
        WorkflowStatus.POST_PUBLISH_VERIFIED: "finalize",
        WorkflowStatus.VERIFIED: snapshot.controller_decisions[-1].selected_next_stage if snapshot.controller_decisions else "acceptance",
        WorkflowStatus.ACCEPTANCE_EVALUATED: snapshot.controller_decisions[-1].selected_next_stage if snapshot.controller_decisions else "finalize",
    }
    return mapping.get(snapshot.status)
