from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

JsonDict = dict[str, Any]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class RuntimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, arbitrary_types_allowed=True)


class Capability(str, Enum):
    DOCUMENT_READ = "document_read"
    REPO_READ = "repo_read"
    REPO_WRITE = "repo_write"
    SHELL_READ = "shell_read"
    SHELL_WRITE = "shell_write"
    GIT_READ = "git_read"
    GIT_WRITE = "git_write"
    HOST_ACCESS = "host_access"
    K8S_READ = "k8s_read"
    K8S_WRITE = "k8s_write"
    NETWORK_DIAGNOSTICS = "network_diagnostics"


class ExecutionFamily(str, Enum):
    DOCUMENTATION_ONLY = "documentation_only"
    REPOSITORY_CHANGE = "repository_change"
    HOST_OPERATION = "host_operation"
    CLUSTER_OPERATION = "cluster_operation"
    NETWORK_INVESTIGATION = "network_investigation"


class Task(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("task"))
    title: str | None = None
    description: str
    repository: str | None = None
    branch: str | None = None
    git_provider: str | None = None
    metadata: JsonDict = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)


class Artifact(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("artifact"))
    kind: str
    path: str
    media_type: str = "text/plain"
    created_at: str = Field(default_factory=utc_now)
    text_preview: str | None = None
    metadata: JsonDict = Field(default_factory=dict)


class ContextSection(RuntimeModel):
    title: str
    body: str
    artifact_id: str | None = None


class ContextPacket(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("ctx"))
    task_id: str
    artifact_ids: list[str] = Field(default_factory=list)
    sections: list[ContextSection] = Field(default_factory=list)
    text: str
    created_at: str = Field(default_factory=utc_now)


class TaskClassification(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("classify"))
    normalized_task: str
    needs_world_facts: bool
    execution_family: ExecutionFamily
    capabilities: list[Capability] = Field(default_factory=list)
    observation_focus: list[str] = Field(default_factory=list)
    reasoning: str
    risk_level: str = "low"


class ObservationRequest(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("observe_req"))
    task_id: str
    execution_family: ExecutionFamily
    capabilities: list[Capability] = Field(default_factory=list)
    prompt: str
    repository: str | None = None
    branch: str | None = None
    git_provider: str | None = None
    metadata: JsonDict = Field(default_factory=dict)


class ObservationResult(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("observe_res"))
    request_id: str
    ok: bool
    summary: str
    evidence_text: str
    artifacts: list[Artifact] = Field(default_factory=list)
    conversation_id: str | None = None
    created_at: str = Field(default_factory=utc_now)


class LLMRequest(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("llm_req"))
    kind: str
    prompt: str
    task_id: str
    context_packet_id: str | None = None
    response_schema: str | None = None
    metadata: JsonDict = Field(default_factory=dict)


class LLMResult(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("llm_res"))
    request_id: str
    ok: bool
    model: str | None = None
    backend: str | None = None
    raw_text: str
    parsed: JsonDict = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)


class ExecutionPlan(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("plan"))
    summary: str
    execution_family: ExecutionFamily
    capabilities: list[Capability] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    verification_checks: list[str] = Field(default_factory=list)
    requires_mutation: bool = False
    reasoning: str


class PolicyDecision(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("policy"))
    allowed: bool
    blocked: bool = False
    requires_approval: bool = False
    reasons: list[str] = Field(default_factory=list)
    execution_family: ExecutionFamily
    capabilities: list[Capability] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)


class ApprovalRequest(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("approval"))
    policy_decision_id: str
    required: bool = True
    rationale: str
    approved: bool | None = None
    reviewer: str | None = None
    resolved_at: str | None = None


class ExecutionRequest(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("exec_req"))
    task_id: str
    execution_family: ExecutionFamily
    capabilities: list[Capability] = Field(default_factory=list)
    prompt: str
    repository: str | None = None
    branch: str | None = None
    git_provider: str | None = None
    plan_summary: str | None = None
    metadata: JsonDict = Field(default_factory=dict)


class ExecutionResult(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("exec_res"))
    request_id: str
    ok: bool
    summary: str
    evidence_text: str
    artifacts: list[Artifact] = Field(default_factory=list)
    conversation_id: str | None = None
    created_at: str = Field(default_factory=utc_now)


class VerificationRequest(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("verify_req"))
    execution_result_id: str
    execution_family: ExecutionFamily
    prompt: str
    repository: str | None = None
    branch: str | None = None
    git_provider: str | None = None
    metadata: JsonDict = Field(default_factory=dict)


class VerificationResult(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("verify_res"))
    request_id: str
    passed: bool
    summary: str
    evidence_text: str
    artifacts: list[Artifact] = Field(default_factory=list)
    conversation_id: str | None = None
    created_at: str = Field(default_factory=utc_now)


class FinalReport(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("report"))
    task_id: str
    status: str
    summary: str
    classification: TaskClassification | None = None
    plan: ExecutionPlan | None = None
    policy: PolicyDecision | None = None
    approval: ApprovalRequest | None = None
    observation: ObservationResult | None = None
    execution: ExecutionResult | None = None
    verification: VerificationResult | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)
