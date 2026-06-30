from __future__ import annotations

__all__ = [
    "WorkflowCheckpointError",
    "WorkflowCheckpointRecorder",
    "wrap_stage_node_with_checkpoint",
    "infer_workspace_root_from_environment_plan",
    "infer_workspace_root_from_execution",
    "infer_workspace_root_from_observation",
    "infer_workspace_root_from_text",
    "workspace_root_from_state",
]


def __getattr__(name: str):
    if name in {"WorkflowCheckpointError", "WorkflowCheckpointRecorder", "wrap_stage_node_with_checkpoint"}:
        from . import checkpoints

        return getattr(checkpoints, name)
    if name in {
        "infer_workspace_root_from_environment_plan",
        "infer_workspace_root_from_execution",
        "infer_workspace_root_from_observation",
        "infer_workspace_root_from_text",
        "workspace_root_from_state",
    }:
        from . import workspace

        return getattr(workspace, name)
    raise AttributeError(name)
