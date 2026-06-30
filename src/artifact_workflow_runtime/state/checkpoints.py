from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.models.state import WorkflowState

StageNode = Callable[[WorkflowState], Awaitable[dict[str, Any]]]


@dataclass
class WorkflowCheckpointRecorder:
    """Persist canonical workflow-state checkpoints after stage nodes.

    This is intentionally independent from the public evidence ArtifactStore
    contract: checkpoint artifacts are debugging/resume primitives and are not
    appended to ``artifact_ids``. LangGraph-native checkpointers can replace or
    complement this recorder later without changing stage code.
    """

    artifact_store: ArtifactStore
    enabled: bool = True

    def record(self, *, stage: str, before: Mapping[str, Any], update: Mapping[str, Any]) -> object | None:
        if not self.enabled:
            return None
        merged: dict[str, Any] = dict(before)
        merged.update(dict(update))
        task = merged.get("task") if isinstance(merged, dict) else None
        task_id = task.get("id") if isinstance(task, dict) else None
        payload = {
            "stage": stage,
            "task_id": task_id,
            "status": merged.get("status"),
            "before_status": before.get("status"),
            "update_keys": sorted(str(key) for key in update.keys()),
            "state": merged,
        }
        return self.artifact_store.add_json(
            "workflow_checkpoint",
            payload,
            metadata={"stage": stage, "task_id": task_id, "status": merged.get("status")},
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
