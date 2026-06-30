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


class VerificationMode(str, Enum):
    EVIDENCE_REVIEW = "evidence_review"
    WORLD_CHECK = "world_check"




class ExecutionStatus(str, Enum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    BLOCKED = "blocked"


class AcceptanceStatus(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    BLOCKED = "blocked"
    NEEDS_ENVIRONMENT = "needs_environment"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


class AcceptanceObligationStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    NOT_RUN = "not_run"


class AcceptanceObligationKind(str, Enum):
    CODE_CHANGED = "code_changed"
    BUILD_OR_COMPILE_SUCCEEDED = "build_or_compile_succeeded"
    RELEVANT_TESTS_RUN = "relevant_tests_run"
    RELEVANT_TESTS_PASSED = "relevant_tests_passed"
    INTEGRATION_TESTS_RUN = "integration_tests_run"
    INTEGRATION_TESTS_PASSED = "integration_tests_passed"
    ENVIRONMENT_PREREQUISITES_SATISFIED = "environment_prerequisites_satisfied"
    PUBLISH_OBLIGATIONS_SATISFIED = "publish_obligations_satisfied"
    REQUIRED_EVIDENCE_PRESENT = "required_evidence_present"


class BlockerKind(str, Enum):
    GENERIC = "generic"
    MISSING_ENVIRONMENT_DEPENDENCY = "missing_environment_dependency"
    MISSING_RUNTIME_PREREQUISITE = "missing_runtime_prerequisite"
    INTEGRATION_ENVIRONMENT_UNAVAILABLE = "integration_environment_unavailable"
    TEST_FAILURE = "test_failure"
    MISSING_EVIDENCE = "missing_evidence"
    POLICY_BLOCKED = "policy_blocked"
    EXECUTION_FAILURE = "execution_failure"


class EnvironmentBlocker(RuntimeModel):
    kind: BlockerKind = BlockerKind.MISSING_ENVIRONMENT_DEPENDENCY
    summary: str
    missing_dependency: str | None = None
    required_for: str | None = None
    evidence_artifact_ids: list[str] = Field(default_factory=list)

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


class CommandEvidence(RuntimeModel):
    command: str
    cwd: str | None = None
    exit_code: int | None = None
    output_excerpt: str | None = None
    output_artifact_ids: list[str] = Field(default_factory=list)


class FileEvidence(RuntimeModel):
    path: str
    action: str = "observed"
    summary: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)


class ExtractedFact(RuntimeModel):
    subject: str
    fact: str
    source: str | None = None
    confidence: str = "medium"
    artifact_ids: list[str] = Field(default_factory=list)


class DiffEvidence(RuntimeModel):
    path: str | None = None
    summary: str
    diff_artifact_ids: list[str] = Field(default_factory=list)


class TestCheckEvidence(RuntimeModel):
    name: str
    command: str | None = None
    passed: bool | None = None
    status: str = "unknown"
    output_excerpt: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)


class BlockerEvidence(RuntimeModel):
    summary: str
    severity: str = "medium"
    blocker_kind: BlockerKind = BlockerKind.GENERIC
    artifact_ids: list[str] = Field(default_factory=list)


class MutationSummary(RuntimeModel):
    changed: bool = False
    summary: str = ""
    files_changed: list[str] = Field(default_factory=list)


class PostcheckSummary(RuntimeModel):
    attempted: bool = False
    summary: str = ""
    checks: list[TestCheckEvidence] = Field(default_factory=list)


class StructuredEvidence(RuntimeModel):
    commands_run: list[CommandEvidence] = Field(default_factory=list)
    files_changed: list[FileEvidence] = Field(default_factory=list)
    files_observed: list[FileEvidence] = Field(default_factory=list)
    extracted_facts: list[ExtractedFact] = Field(default_factory=list)
    diffs: list[DiffEvidence] = Field(default_factory=list)
    tests: list[TestCheckEvidence] = Field(default_factory=list)
    blockers: list[BlockerEvidence] = Field(default_factory=list)
    mutation_summary: MutationSummary = Field(default_factory=MutationSummary)
    postcheck_summary: PostcheckSummary = Field(default_factory=PostcheckSummary)


class EvidenceBundle(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("evidence"))
    source_backend: BackendKind
    work_packet_kind: WorkPacketKind
    ok: bool
    summary: str
    artifact_ids: list[str] = Field(default_factory=list)
    structured: StructuredEvidence = Field(default_factory=StructuredEvidence)
    evidence_kind: str = "agent_text"
    raw_text_artifact_id: str | None = None
    structured_artifact_id: str | None = None
    blockers: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)

    def operational_summary(self) -> str:
        parts: list[str] = []
        if self.structured.commands_run:
            parts.append(f"commands={len(self.structured.commands_run)}")
        if self.structured.files_changed:
            parts.append(f"files_changed={len(self.structured.files_changed)}")
        if self.structured.files_observed:
            parts.append(f"files_observed={len(self.structured.files_observed)}")
        if self.structured.tests:
            statuses = ",".join(sorted({item.status for item in self.structured.tests}))
            parts.append(f"checks={len(self.structured.tests)}[{statuses}]")
        if self.structured.blockers:
            parts.append(f"blockers={len(self.structured.blockers)}")
        return "; ".join(parts) or self.summary


class ResponseFieldExpectation(RuntimeModel):
    name: str
    required: bool = True
    description: str | None = None


class StructuredResponseContract(RuntimeModel):
    """Machine-usable response contract; prompt text is only a rendering of this."""

    response_format: str = "json"
    required_fields: list[ResponseFieldExpectation] = Field(default_factory=list)
    evidence_first: bool = True
    raw_text_allowed_as_supplement: bool = True
    notes: list[str] = Field(default_factory=list)

    @classmethod
    def for_fields(cls, *fields: str, notes: list[str] | None = None) -> "StructuredResponseContract":
        return cls(required_fields=[ResponseFieldExpectation(name=field) for field in fields], notes=notes or [])

    def render(self) -> str:
        field_lines = []
        for field in self.required_fields:
            suffix = "required" if field.required else "optional"
            detail = f" - {field.description}" if field.description else ""
            field_lines.append(f"- {field.name} ({suffix}){detail}")
        return "\n".join([
            f"response_format: {self.response_format}",
            f"evidence_first: {self.evidence_first}",
            f"raw_text_allowed_as_supplement: {self.raw_text_allowed_as_supplement}",
            "required_fields:",
            *(field_lines or ["- none declared"]),
            *( ["notes:", *[f"- {note}" for note in self.notes]] if self.notes else [] ),
        ])


class EvidenceRequirements(RuntimeModel):
    """What operational evidence the controller expects from a backend packet."""

    require_structured: bool = True
    raw_text_role: str = "supplement"
    required_sections: list[str] = Field(default_factory=list)
    required_artifact_kinds: list[str] = Field(default_factory=list)
    source_artifact_ids: list[str] = Field(default_factory=list)
    require_commands: bool = False
    require_files: bool = False
    require_facts: bool = False
    require_diffs: bool = False
    require_tests: bool = False
    require_blockers: bool = True

    def render(self) -> str:
        flags = {
            "commands": self.require_commands,
            "files": self.require_files,
            "facts": self.require_facts,
            "diffs": self.require_diffs,
            "tests": self.require_tests,
            "blockers": self.require_blockers,
        }
        required = [name for name, enabled in flags.items() if enabled]
        lines = [
            f"require_structured: {self.require_structured}",
            f"raw_text_role: {self.raw_text_role}",
            f"required_evidence_types: {required}",
            f"required_sections: {self.required_sections}",
            f"required_artifact_kinds: {self.required_artifact_kinds}",
        ]
        if self.source_artifact_ids:
            lines.append(f"source_artifact_ids: {self.source_artifact_ids}")
        return "\n".join(lines)


def _render_compiled_contract(*, title: str, fields: dict[str, object], narrative: str) -> str:
    lines = [f"# {title}", "", "## Typed contract"]
    for key, value in fields.items():
        if value is None or value == [] or value == {}:
            continue
        lines.append(f"{key}: {value}")
    if narrative.strip():
        lines.extend(["", "## Compiled narrative instructions", narrative.strip()])
    return "\n".join(lines).strip()


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


class AcceptanceObligation(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("accept_obl"))
    kind: AcceptanceObligationKind
    name: str
    required: bool = True
    blocking: bool = True
    source: str = "controller"
    checks: list[str] = Field(default_factory=list)
    required_environment: list[str] = Field(default_factory=list)


class TaskAcceptanceContract(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("accept_contract"))
    task_id: str
    execution_family: ExecutionFamily
    requires_mutation: bool = False
    mutation_requires_verification: bool = True
    completion_rule: str = "all_required_blocking_obligations_pass"
    obligations: list[AcceptanceObligation] = Field(default_factory=list)
    required_environment_prerequisites: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)


class VerificationObligationResult(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("accept_obl_res"))
    obligation_id: str
    obligation_name: str
    kind: AcceptanceObligationKind
    status: AcceptanceObligationStatus
    reason: str
    evidence_artifact_ids: list[str] = Field(default_factory=list)
    blocker_kind: BlockerKind | None = None
    environment_blocker: EnvironmentBlocker | None = None


class AcceptanceDecision(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("accept_decision"))
    contract_id: str
    status: AcceptanceStatus
    accepted: bool
    execution_status: ExecutionStatus
    final_workflow_status: str
    summary: str
    obligation_results: list[VerificationObligationResult] = Field(default_factory=list)
    blockers: list[EnvironmentBlocker] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)


class ObservationRequest(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("observe_req"))
    task_id: str
    execution_family: ExecutionFamily
    work_packet_kind: WorkPacketKind = WorkPacketKind.OBSERVE
    capabilities: list[Capability] = Field(default_factory=list)
    prompt: str
    objective: str = "collect world facts"
    focus: list[str] = Field(default_factory=list)
    required_facts: list[str] = Field(default_factory=list)
    scope_constraints: list[str] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=lambda: ["read_files", "run_read_only_commands", "inspect_repo", "inspect_runtime_state"] )
    forbidden_actions: list[str] = Field(default_factory=lambda: ["edit_files", "commit", "push", "apply_cluster_changes", "change_host_config"] )
    expected_outputs: list[str] = Field(default_factory=lambda: ["facts", "commands_run", "outputs", "blockers", "unknowns"] )
    evidence_requirements: EvidenceRequirements = Field(default_factory=lambda: EvidenceRequirements(require_facts=True, require_commands=True, required_sections=["facts", "commands_run", "files_observed", "blockers", "unknowns"]))
    response_contract: StructuredResponseContract = Field(default_factory=lambda: StructuredResponseContract.for_fields("summary", "structured_evidence", "blockers", notes=["OpenHands may include raw text, but structured_evidence is the operational output."]))
    metadata: JsonDict = Field(default_factory=dict)

    def compiled_prompt(self) -> str:
        return _render_compiled_contract(
            title="Bounded OpenHands observation packet",
            fields={
                "packet_kind": self.work_packet_kind.value,
                "task_id": self.task_id,
                "objective": self.objective,
                "execution_family": self.execution_family.value,
                "capabilities": [cap.value for cap in self.capabilities],
                "focus": self.focus,
                "required_facts": self.required_facts,
                "scope_constraints": self.scope_constraints,
                "allowed_actions": self.allowed_actions,
                "forbidden_actions": self.forbidden_actions,
                "expected_outputs": self.expected_outputs,
                "evidence_requirements": "\n" + self.evidence_requirements.render(),
                "response_contract": "\n" + self.response_contract.render(),
            },
            narrative=self.prompt,
        )

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
    structured_evidence: StructuredEvidence = Field(default_factory=StructuredEvidence)
    evidence_bundle: EvidenceBundle | None = None
    primary_evidence_artifact_ids: list[str] = Field(default_factory=list)
    raw_evidence_artifact_id: str | None = None
    conversation_id: str | None = None
    transport_error: bool = False
    evidence_kind: str = "agent_text"
    created_at: str = Field(default_factory=utc_now)


class LLMRequest(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("llm_req"))
    kind: str
    prompt: str
    task_id: str
    purpose: str = "text-only reasoning"
    task_text: str | None = None
    instructions: list[str] = Field(default_factory=list)
    input_artifact_ids: list[str] = Field(default_factory=list)
    backend: BackendKind = BackendKind.DIRECT_LLM
    context_packet_id: str | None = None
    response_schema: str | None = None
    allowed_inputs: list[str] = Field(default_factory=lambda: ["task_text", "context_packet_text", "schema_text"] )
    forbidden_inputs: list[str] = Field(default_factory=lambda: ["filesystem", "shell", "git", "hosts", "kubernetes", "network_runtime_state"] )
    response_contract: StructuredResponseContract = Field(default_factory=lambda: StructuredResponseContract.for_fields("valid_json", notes=["Direct LLM receives text only; no world access is available."]))
    metadata: JsonDict = Field(default_factory=dict)

    def compiled_prompt(self) -> str:
        return _render_compiled_contract(
            title="Direct LLM text-only reasoning request",
            fields={
                "kind": self.kind,
                "purpose": self.purpose,
                "task_id": self.task_id,
                "backend": self.backend.value,
                "context_packet_id": self.context_packet_id,
                "input_artifact_ids": self.input_artifact_ids,
                "allowed_inputs": self.allowed_inputs,
                "forbidden_inputs": self.forbidden_inputs,
                "instructions": self.instructions,
                "response_schema": self.response_schema,
                "response_contract": "\n" + self.response_contract.render(),
                "task_text": self.task_text,
            },
            narrative=self.prompt,
        )


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
    objective: str = "execute approved plan"
    plan_steps: list[str] = Field(default_factory=list)
    expected_changes: list[str] = Field(default_factory=list)
    verification_commands: list[str] = Field(default_factory=list)
    scope_constraints: list[str] = Field(default_factory=list)
    plan_summary: str | None = None
    context_packet_id: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=lambda: ["edit_files", "run_commands", "run_tests", "use_git_when_requested", "collect_evidence"] )
    forbidden_actions: list[str] = Field(default_factory=lambda: ["change_workflow_decision", "skip_required_evidence", "act_outside_capabilities"] )
    expected_outputs: list[str] = Field(default_factory=lambda: ["changed_files", "commands_run", "test_results", "blockers"] )
    success_criteria: list[str] = Field(default_factory=list)
    evidence_requirements: EvidenceRequirements = Field(default_factory=lambda: EvidenceRequirements(require_commands=True, require_files=True, require_tests=True, require_blockers=True, required_sections=["commands_run", "files_changed", "tests", "blockers", "mutation_summary", "postcheck_summary"]))
    response_contract: StructuredResponseContract = Field(default_factory=lambda: StructuredResponseContract.for_fields("summary", "structured_evidence", "mutation_summary", "postcheck_summary", "blockers"))
    metadata: JsonDict = Field(default_factory=dict)

    def compiled_prompt(self) -> str:
        return _render_compiled_contract(
            title="Bounded OpenHands execution packet",
            fields={
                "packet_kind": self.work_packet_kind.value,
                "task_id": self.task_id,
                "objective": self.objective,
                "execution_family": self.execution_family.value,
                "capabilities": [cap.value for cap in self.capabilities],
                "plan_summary": self.plan_summary,
                "plan_steps": self.plan_steps,
                "expected_changes": self.expected_changes,
                "verification_commands": self.verification_commands,
                "success_criteria": self.success_criteria,
                "context_packet_id": self.context_packet_id,
                "artifact_ids": self.artifact_ids,
                "scope_constraints": self.scope_constraints,
                "allowed_actions": self.allowed_actions,
                "forbidden_actions": self.forbidden_actions,
                "expected_outputs": self.expected_outputs,
                "evidence_requirements": "\n" + self.evidence_requirements.render(),
                "response_contract": "\n" + self.response_contract.render(),
            },
            narrative=self.prompt,
        )

    @field_validator("capabilities", mode="before")
    @classmethod
    def _validate_capabilities(cls, value: object) -> list[Capability]:
        return _normalize_capability_list(value)


class ExecutionResult(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("exec_res"))
    request_id: str
    ok: bool
    execution_status: ExecutionStatus = ExecutionStatus.SUCCEEDED
    summary: str
    evidence_text: str
    artifacts: list[Artifact] = Field(default_factory=list)
    structured_evidence: StructuredEvidence = Field(default_factory=StructuredEvidence)
    evidence_bundle: EvidenceBundle | None = None
    primary_evidence_artifact_ids: list[str] = Field(default_factory=list)
    raw_evidence_artifact_id: str | None = None
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
    objective: str = "complete repository publication obligations"
    allowed_actions: list[str] = Field(default_factory=lambda: ["inspect_git", "commit_when_required", "push_when_required", "inspect_pr_checks", "collect_evidence"] )
    forbidden_actions: list[str] = Field(default_factory=lambda: ["change_workflow_decision", "expand_task_scope", "skip_required_pr_checks"] )
    require_commit: bool = False
    require_push: bool = False
    artifact_ids: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=lambda: ["git_status", "commit_hashes", "push_result", "pr_url", "check_statuses", "blockers"] )
    evidence_requirements: EvidenceRequirements = Field(default_factory=lambda: EvidenceRequirements(require_commands=True, require_tests=True, require_blockers=True, required_sections=["git_status", "commit_hashes", "push_result", "pr_checks", "blockers"]))
    response_contract: StructuredResponseContract = Field(default_factory=lambda: StructuredResponseContract.for_fields("summary", "structured_evidence", "postcheck_summary", "blockers"))
    metadata: JsonDict = Field(default_factory=dict)

    def compiled_prompt(self) -> str:
        return _render_compiled_contract(
            title="Bounded OpenHands publish packet",
            fields={
                "packet_kind": self.work_packet_kind.value,
                "task_id": self.task_id,
                "execution_result_id": self.execution_result_id,
                "objective": self.objective,
                "require_commit": self.require_commit,
                "require_push": self.require_push,
                "artifact_ids": self.artifact_ids,
                "allowed_actions": self.allowed_actions,
                "forbidden_actions": self.forbidden_actions,
                "expected_outputs": self.expected_outputs,
                "evidence_requirements": "\n" + self.evidence_requirements.render(),
                "response_contract": "\n" + self.response_contract.render(),
            },
            narrative=self.prompt,
        )


class PublishResult(RuntimeModel):
    id: str = Field(default_factory=lambda: new_id("publish_res"))
    request_id: str
    ok: bool
    summary: str
    evidence_text: str
    artifacts: list[Artifact] = Field(default_factory=list)
    structured_evidence: StructuredEvidence = Field(default_factory=StructuredEvidence)
    evidence_bundle: EvidenceBundle | None = None
    primary_evidence_artifact_ids: list[str] = Field(default_factory=list)
    raw_evidence_artifact_id: str | None = None
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
    allowed_inputs: list[str] = Field(default_factory=lambda: ["task_text", "context_packet_text", "structured_evidence", "schema_text"] )
    forbidden_inputs: list[str] = Field(default_factory=lambda: ["filesystem", "shell", "git", "hosts", "kubernetes", "network_runtime_state"] )
    response_contract: StructuredResponseContract = Field(default_factory=lambda: StructuredResponseContract.for_fields("passed", "summary", "missing_evidence", "confidence"))
    metadata: JsonDict = Field(default_factory=dict)

    def compiled_prompt(self) -> str:
        return _render_compiled_contract(
            title="Per-check evidence-review request",
            fields={
                "parent_request_id": self.parent_request_id,
                "task_id": self.task_id,
                "execution_result_id": self.execution_result_id,
                "execution_family": self.execution_family.value,
                "check_name": self.check_name,
                "normalized_check": self.normalized_check,
                "backend": self.backend.value,
                "context_packet_id": self.context_packet_id,
                "artifact_ids": self.artifact_ids,
                "allowed_inputs": self.allowed_inputs,
                "forbidden_inputs": self.forbidden_inputs,
                "response_contract": "\n" + self.response_contract.render(),
            },
            narrative=self.prompt,
        )


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
    backend: BackendKind = BackendKind.DIRECT_LLM
    mode: VerificationMode = VerificationMode.EVIDENCE_REVIEW
    prompt: str
    artifact_ids: list[str] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)
    allowed_inputs: list[str] = Field(default_factory=lambda: ["task_text", "context_packet_text", "structured_evidence", "artifact_text"] )
    forbidden_inputs: list[str] = Field(default_factory=lambda: ["filesystem", "shell", "git", "hosts", "kubernetes", "network_runtime_state"] )
    expected_outputs: list[str] = Field(default_factory=lambda: ["checks_passed", "checks_failed", "missing_evidence", "completion_status"] )
    evidence_requirements: EvidenceRequirements = Field(default_factory=lambda: EvidenceRequirements(require_structured=True, require_tests=True, require_blockers=True, required_sections=["checks", "missing_evidence", "completion_status", "blockers"]))
    response_contract: StructuredResponseContract = Field(default_factory=lambda: StructuredResponseContract.for_fields("passed", "summary", "checks_passed", "checks_failed", "missing_evidence", "completion_status"))
    metadata: JsonDict = Field(default_factory=dict)

    def compiled_prompt(self) -> str:
        return _render_compiled_contract(
            title="Verification request",
            fields={
                "packet_kind": self.work_packet_kind.value,
                "execution_result_id": self.execution_result_id,
                "execution_family": self.execution_family.value,
                "backend": self.backend.value,
                "mode": self.mode.value,
                "artifact_ids": self.artifact_ids,
                "checks": self.checks,
                "allowed_inputs": self.allowed_inputs,
                "forbidden_inputs": self.forbidden_inputs,
                "expected_outputs": self.expected_outputs,
                "evidence_requirements": "\n" + self.evidence_requirements.render(),
                "response_contract": "\n" + self.response_contract.render(),
            },
            narrative=self.prompt,
        )


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
    acceptance_status: AcceptanceStatus | None = None
    obligation_results: list[VerificationObligationResult] = Field(default_factory=list)
    summary: str
    evidence_text: str
    artifacts: list[Artifact] = Field(default_factory=list)
    structured_evidence: StructuredEvidence = Field(default_factory=StructuredEvidence)
    evidence_bundle: EvidenceBundle | None = None
    primary_evidence_artifact_ids: list[str] = Field(default_factory=list)
    raw_evidence_artifact_id: str | None = None
    conversation_id: str | None = None
    mode: VerificationMode = VerificationMode.EVIDENCE_REVIEW
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
    acceptance_contract: TaskAcceptanceContract | None = None
    acceptance_decision: AcceptanceDecision | None = None
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
