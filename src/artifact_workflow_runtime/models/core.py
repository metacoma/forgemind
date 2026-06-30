from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    REPO_CREATE_PR = "repo_create_pr"
    SHELL_READ = "shell_read"
    SHELL_WRITE = "shell_write"
    GIT_READ = "git_read"
    GIT_WRITE = "git_write"
    HOST_ACCESS = "host_access"
    K8S_READ = "k8s_read"
    K8S_WRITE = "k8s_write"
    NETWORK_DIAGNOSTICS = "network_diagnostics"

    @classmethod
    def coerce(cls, value: object) -> "Capability | None":
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            return None
        key = value.strip().lower().replace("-", "_").replace(" ", "_")
        aliases: dict[str, Capability] = {
            "repo_create_pr": cls.REPO_CREATE_PR,
            "create_pr": cls.REPO_CREATE_PR,
            "pull_request": cls.REPO_CREATE_PR,
            "pr": cls.REPO_CREATE_PR,
            "repo_push": cls.GIT_WRITE,
            "git_push": cls.GIT_WRITE,
            "push": cls.GIT_WRITE,
            "git_commit": cls.GIT_WRITE,
            "commit": cls.GIT_WRITE,
            "repo_commit": cls.GIT_WRITE,
            "repo_rw": cls.REPO_WRITE,
            "repo_ro": cls.REPO_READ,
            "shell_execute": cls.SHELL_WRITE,
            "run_shell": cls.SHELL_WRITE,
            "kubernetes_read": cls.K8S_READ,
            "kubernetes_write": cls.K8S_WRITE,
            "net_diag": cls.NETWORK_DIAGNOSTICS,
        }
        mapped = aliases.get(key)
        if mapped is not None:
            return mapped
        for member in cls:
            if key == member.value or key == member.name.lower():
                return member
        return None


def _normalize_capability_list(value: object) -> list[Capability]:
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    normalized: list[Capability] = []
    for item in items:
        cap = Capability.coerce(item)
        if cap is None or cap in normalized:
            continue
        normalized.append(cap)
    return normalized


class ExecutionFamily(str, Enum):
    DOCUMENTATION_ONLY = "documentation_only"
    REPOSITORY_CHANGE = "repository_change"
    HOST_OPERATION = "host_operation"
    CLUSTER_OPERATION = "cluster_operation"
    NETWORK_INVESTIGATION = "network_investigation"


class BackendKind(str, Enum):
    DIRECT_LLM = "direct_llm"
    OPENHANDS = "openhands"


class WorkPacketKind(str, Enum):
    RESEARCH = "research"
    OBSERVE = "observe"
    EXECUTE = "execute"
    PUBLISH = "publish"
    VERIFY = "verify"


class Task(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("task"))
    title: str | None = None
    description: str
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


class EvidenceBundle(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("evidence"))
    source_backend: BackendKind
    work_packet_kind: WorkPacketKind
    ok: bool
    summary: str
    artifact_ids: list[str] = Field(default_factory=list)
    evidence_kind: str = "agent_text"
    blockers: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)


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
    task_intent: str = "investigate"
    capabilities: list[Capability] = Field(default_factory=list)
    observation_focus: list[str] = Field(default_factory=list)
    reasoning: str
    risk_level: str = "low"

    @field_validator("capabilities", mode="before")
    @classmethod
    def _validate_capabilities(cls, value: object) -> list[Capability]:
        return _normalize_capability_list(value)


class RoutingDecision(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("route"))
    needs_repository_observation: bool = False
    needs_world_observation: bool = False
    needs_fresh_external_research: bool = False
    can_plan_immediately: bool = True
    required_evidence_types: list[str] = Field(default_factory=list)
    research_targets: list[str] = Field(default_factory=list)
    observation_focus: list[str] = Field(default_factory=list)
    reasoning: str




class ObligationAnalysis(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("obligations"))
    required_test_levels: list[str] = Field(default_factory=list)
    required_setup_steps: list[str] = Field(default_factory=list)
    required_environment_conditions: list[str] = Field(default_factory=list)
    required_publish_actions: list[str] = Field(default_factory=list)
    completion_requirements: list[str] = Field(default_factory=list)
    blocker_conditions: list[str] = Field(default_factory=list)
    reasoning_summary: str


class ObservationRequest(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("observe_req"))
    task_id: str
    execution_family: ExecutionFamily
    work_packet_kind: WorkPacketKind = WorkPacketKind.OBSERVE
    capabilities: list[Capability] = Field(default_factory=list)
    prompt: str
    allowed_actions: list[str] = Field(default_factory=lambda: ["read_files", "run_read_only_commands", "inspect_repo", "inspect_runtime_state"] )
    forbidden_actions: list[str] = Field(default_factory=lambda: ["edit_files", "commit", "push", "apply_cluster_changes", "change_host_config"] )
    expected_outputs: list[str] = Field(default_factory=lambda: ["facts", "commands_run", "outputs", "blockers", "unknowns"] )
    metadata: JsonDict = Field(default_factory=dict)

    @field_validator("capabilities", mode="before")
    @classmethod
    def _validate_capabilities(cls, value: object) -> list[Capability]:
        return _normalize_capability_list(value)


class ObservationResult(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("observe_res"))
    request_id: str
    ok: bool
    summary: str
    evidence_text: str
    artifacts: list[Artifact] = Field(default_factory=list)
    conversation_id: str | None = None
    transport_error: bool = False
    evidence_kind: str = "agent_text"
    created_at: str = Field(default_factory=utc_now)


class LLMRequest(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("llm_req"))
    kind: str
    prompt: str
    task_id: str
    backend: BackendKind = BackendKind.DIRECT_LLM
    context_packet_id: str | None = None
    response_schema: str | None = None
    allowed_inputs: list[str] = Field(default_factory=lambda: ["task_text", "context_packet_text", "schema_text"] )
    forbidden_inputs: list[str] = Field(default_factory=lambda: ["filesystem", "shell", "git", "hosts", "kubernetes", "network_runtime_state"] )
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
    task_intent: str = "investigate"
    deliverable_kind: str = "analysis"
    capabilities: list[Capability] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    publication_steps: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    verification_checks: list[str] = Field(default_factory=list)
    requires_mutation: bool = False
    must_change_world: bool = False
    expected_repo_changes: list[str] = Field(default_factory=list)
    required_test_levels: list[str] = Field(default_factory=list)
    required_setup_steps: list[str] = Field(default_factory=list)
    require_commit: bool = False
    require_push: bool = False
    execution_environment: str = "docker_container"
    environment_notes: list[str] = Field(default_factory=list)
    reasoning: str

    @field_validator("capabilities", mode="before")
    @classmethod
    def _validate_capabilities(cls, value: object) -> list[Capability]:
        return _normalize_capability_list(value)


class PolicyDecision(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("policy"))
    allowed: bool
    blocked: bool = False
    requires_approval: bool = False
    reasons: list[str] = Field(default_factory=list)
    execution_family: ExecutionFamily
    capabilities: list[Capability] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)

    @field_validator("capabilities", mode="before")
    @classmethod
    def _validate_capabilities(cls, value: object) -> list[Capability]:
        return _normalize_capability_list(value)


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
    work_packet_kind: WorkPacketKind = WorkPacketKind.EXECUTE
    capabilities: list[Capability] = Field(default_factory=list)
    prompt: str
    plan_summary: str | None = None
    context_packet_id: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=lambda: ["edit_files", "run_commands", "run_tests", "use_git_when_requested", "collect_evidence"] )
    forbidden_actions: list[str] = Field(default_factory=lambda: ["change_workflow_decision", "skip_required_evidence", "act_outside_capabilities"] )
    expected_outputs: list[str] = Field(default_factory=lambda: ["changed_files", "commands_run", "test_results", "blockers"] )
    success_criteria: list[str] = Field(default_factory=list)
    metadata: JsonDict = Field(default_factory=dict)

    @field_validator("capabilities", mode="before")
    @classmethod
    def _validate_capabilities(cls, value: object) -> list[Capability]:
        return _normalize_capability_list(value)


class ExecutionResult(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("exec_res"))
    request_id: str
    ok: bool
    summary: str
    evidence_text: str
    artifacts: list[Artifact] = Field(default_factory=list)
    conversation_id: str | None = None
    transport_error: bool = False
    evidence_kind: str = "agent_text"
    created_at: str = Field(default_factory=utc_now)


class PublishRequest(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("publish_req"))
    execution_result_id: str
    task_id: str
    work_packet_kind: WorkPacketKind = WorkPacketKind.PUBLISH
    prompt: str
    require_commit: bool = False
    require_push: bool = False
    artifact_ids: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=lambda: ["git_status", "commit_hashes", "push_result", "pr_url", "check_statuses", "blockers"] )
    metadata: JsonDict = Field(default_factory=dict)


class PublishResult(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("publish_res"))
    request_id: str
    ok: bool
    summary: str
    evidence_text: str
    artifacts: list[Artifact] = Field(default_factory=list)
    conversation_id: str | None = None
    transport_error: bool = False
    evidence_kind: str = "agent_text"
    created_at: str = Field(default_factory=utc_now)


class VerificationCheckRequest(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("verify_check_req"))
    parent_request_id: str
    task_id: str
    execution_result_id: str
    execution_family: ExecutionFamily
    check_name: str
    normalized_check: str
    backend: BackendKind = BackendKind.DIRECT_LLM
    prompt: str
    context_packet_id: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    allowed_inputs: list[str] = Field(default_factory=lambda: ["task_text", "context_packet_text", "execution_evidence_text", "publish_evidence_text", "schema_text"] )
    forbidden_inputs: list[str] = Field(default_factory=lambda: ["filesystem", "shell", "git", "hosts", "kubernetes", "network_runtime_state"] )
    metadata: JsonDict = Field(default_factory=dict)


class VerificationCheckResult(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("verify_check_res"))
    request_id: str
    check_name: str
    normalized_check: str
    passed: bool
    summary: str
    evidence_text: str
    missing_evidence: list[str] = Field(default_factory=list)
    confidence: str = "low"
    model: str | None = None
    verifier_backend: str = "direct_llm"
    llm_request_id: str | None = None
    created_at: str = Field(default_factory=utc_now)


class VerificationRequest(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("verify_req"))
    execution_result_id: str
    execution_family: ExecutionFamily
    work_packet_kind: WorkPacketKind = WorkPacketKind.VERIFY
    prompt: str
    artifact_ids: list[str] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)
    metadata: JsonDict = Field(default_factory=dict)


class EvidenceVerification(RuntimeModel):
    passed: bool
    summary: str
    checks_passed: list[str] = Field(default_factory=list)
    checks_failed: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    confidence: str = "low"
    reasoning: str
    performed_test_levels: list[str] = Field(default_factory=list)
    missing_test_levels: list[str] = Field(default_factory=list)
    setup_steps_performed: list[str] = Field(default_factory=list)
    missing_setup_steps: list[str] = Field(default_factory=list)
    commit_required: bool = False
    push_required: bool = False
    commit_done: bool = False
    push_done: bool = False
    pr_detected: bool = False
    pr_checks_waited: bool = False
    pr_checks_passed: list[str] = Field(default_factory=list)
    pr_checks_failed: list[str] = Field(default_factory=list)
    pr_checks_pending: list[str] = Field(default_factory=list)
    missing_obligations: list[str] = Field(default_factory=list)
    completion_status: str = "partially_completed"


class VerificationResult(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("verify_res"))
    request_id: str
    passed: bool
    summary: str
    evidence_text: str
    artifacts: list[Artifact] = Field(default_factory=list)
    conversation_id: str | None = None
    checks_passed: list[str] = Field(default_factory=list)
    checks_failed: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    confidence: str = "low"
    verifier_backend: str = "evidence_llm"
    performed_test_levels: list[str] = Field(default_factory=list)
    missing_test_levels: list[str] = Field(default_factory=list)
    setup_steps_performed: list[str] = Field(default_factory=list)
    missing_setup_steps: list[str] = Field(default_factory=list)
    commit_required: bool = False
    push_required: bool = False
    commit_done: bool = False
    push_done: bool = False
    pr_detected: bool = False
    pr_checks_waited: bool = False
    pr_checks_passed: list[str] = Field(default_factory=list)
    pr_checks_failed: list[str] = Field(default_factory=list)
    pr_checks_pending: list[str] = Field(default_factory=list)
    missing_obligations: list[str] = Field(default_factory=list)
    completion_status: str = "partially_completed"
    created_at: str = Field(default_factory=utc_now)


class FinalReport(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("report"))
    task_id: str
    status: str
    summary: str
    classification: TaskClassification | None = None
    route: RoutingDecision | None = None
    obligations: ObligationAnalysis | None = None
    plan: ExecutionPlan | None = None
    policy: PolicyDecision | None = None
    approval: ApprovalRequest | None = None
    research: ObservationResult | None = None
    observation: ObservationResult | None = None
    execution: ExecutionResult | None = None
    publish: PublishResult | None = None
    verification: VerificationResult | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)
