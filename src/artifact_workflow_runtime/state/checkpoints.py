from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.models.state import WorkflowState, validate_workflow_state

StageNode = Callable[[WorkflowState], Awaitable[dict[str, Any]]]


class WorkflowCheckpointError(RuntimeError):
    """Raised when a stage produced an invalid typed workflow snapshot."""


@dataclass
class WorkflowCheckpointRecorder:
    """Persist canonical workflow-state checkpoints after stage nodes.

    Checkpoints are intentionally not added to ``artifact_ids`` because they are
    runtime/debugging state, not operational evidence. They still validate the
    merged graph update against ``WorkflowStateSnapshot`` so a stage cannot leave
    behind a half-shaped state that later fails far away from the source node.
    """

    artifact_store: ArtifactStore
    enabled: bool = True

    def record(self, *, stage: str, before: Mapping[str, Any], update: Mapping[str, Any]) -> object | None:
        if not self.enabled:
            return None
        merged: dict[str, Any] = dict(before)
        merged.update(dict(update))
        try:
            snapshot = validate_workflow_state(merged)
        except Exception as exc:  # noqa: BLE001 - surface exact state boundary error with stage context
            raise WorkflowCheckpointError(f"Stage {stage!r} produced invalid workflow state: {exc}") from exc
        task_id = snapshot.task.id
        payload = {
            "stage": stage,
            "task_id": task_id,
            "status": snapshot.status.value,
            "before_status": before.get("status"),
            "update_keys": sorted(str(key) for key in update.keys()),
            "state": snapshot.model_dump(mode="json"),
            "validated": True,
        }
        return self.artifact_store.add_json(
            "workflow_checkpoint",
            payload,
            metadata={"stage": stage, "task_id": task_id, "status": snapshot.status.value, "validated": True},
        )


def wrap_stage_node_with_checkpoint(stage: str, node: StageNode, recorder: WorkflowCheckpointRecorder | None) -> StageNode:
    """Return a node wrapper that persists a checkpoint after successful output."""

    if recorder is None or not recorder.enabled:
        return node

    async def wrapped(state: WorkflowState) -> dict[str, Any]:
        update = await node(state)
        recorder.record(stage=stage, before=state, update=update)
        return update

    return wrapped
