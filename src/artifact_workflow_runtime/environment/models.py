from __future__ import annotations

from pydantic import Field

from artifact_workflow_runtime.models.base import RuntimeModel, new_id, utc_now


class EnvironmentPlanItem(RuntimeModel):
    name: str
    required_for: list[str] = Field(default_factory=list)
    already_present: bool = False
    bootstrap_possible: bool = False
    bootstrap_source: str | None = None
    bootstrap_command: str | None = None
    bootstrap_attempted: bool = False
    bootstrap_status: str = "not_attempted"
    runtime_usable: bool = False
    runtime_probe_command: str | None = None
    resolved_version: str | None = None
    evidence_artifact_ids: list[str] = Field(default_factory=list)
    failure_mode: str = "needs_environment"
    metadata: dict[str, object] = Field(default_factory=dict)


class EnvironmentPlan(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("env_plan"))
    task_id: str
    workspace_branch: str | None = None
    workspace_root: str | None = None
    items: list[EnvironmentPlanItem] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)
