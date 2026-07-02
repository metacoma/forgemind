from __future__ import annotations

from pydantic import Field

from artifact_workflow_runtime.models.base import RuntimeModel, new_id, utc_now


class EnvironmentAction(RuntimeModel):
    command: str
    resolution: str = "none"
    source: str | None = None
    source_kind: str | None = None
    file_path: str | None = None
    packet_types: list[str] = Field(default_factory=list)


class EnvironmentPlanItem(RuntimeModel):
    name: str
    dependency_kind: str = "generic"
    required_for: list[str] = Field(default_factory=list)
    applicable_packet_types: list[str] = Field(default_factory=list)
    required_verification_levels: list[str] = Field(default_factory=list)
    already_present: bool = False
    bootstrap_possible: bool = False
    bootstrap_source: str | None = None
    bootstrap_resolution: str = "none"
    bootstrap_command: str | None = None
    bootstrap_source_kind: str | None = None
    bootstrap_candidates: list[str] = Field(default_factory=list)
    bootstrap_actions: list[EnvironmentAction] = Field(default_factory=list)
    bootstrap_attempted: bool = False
    bootstrap_status: str = "not_attempted"
    runtime_usable: bool = False
    runtime_probe_resolution: str = "none"
    runtime_probe_command: str | None = None
    runtime_probe_source_kind: str | None = None
    runtime_probe_candidates: list[str] = Field(default_factory=list)
    runtime_probe_actions: list[EnvironmentAction] = Field(default_factory=list)
    failure_mode: str = "needs_environment"


class EnvironmentPlan(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("env_plan"))
    task_id: str
    workspace_branch: str | None = None
    workspace_root: str | None = None
    items: list[EnvironmentPlanItem] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)
