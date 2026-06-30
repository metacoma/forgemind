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
    failure_mode: str = "needs_environment"


class EnvironmentPlan(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("env_plan"))
    task_id: str
    workspace_branch: str | None = None
    workspace_root: str | None = None
    items: list[EnvironmentPlanItem] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)
