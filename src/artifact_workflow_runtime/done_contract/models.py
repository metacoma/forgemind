from __future__ import annotations

from pydantic import Field

from artifact_workflow_runtime.models.base import RuntimeModel, new_id, utc_now


class RuntimeProofPolicy(RuntimeModel):
    required: bool = False
    allow_debt: bool = False
    preferred_level: str = "existing_harness"


class EnvironmentRequirement(RuntimeModel):
    name: str
    mode: str = "required"
    source: str = "task"


class DoneContract(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("done_contract"))
    task_id: str
    primary_goal: str
    change_class: str = "generic_change"
    deliverables: list[str] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)
    verification_policy: RuntimeProofPolicy = Field(default_factory=RuntimeProofPolicy)
    environment_requirements: list[EnvironmentRequirement] = Field(default_factory=list)
    ci_requirements: list[str] = Field(default_factory=list)
    docs_examples_requirements: list[str] = Field(default_factory=list)
    publish_required: bool = False
    notes: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)
