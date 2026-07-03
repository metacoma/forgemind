from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

JsonDict = dict[str, Any]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RuntimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, arbitrary_types_allowed=True)


class ExecutionPacketStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class ExecutionPacketType(str, Enum):
    IMPLEMENTATION = "implementation"
    TEST = "test"
    DOCS = "docs"
    VERIFICATION = "verification"
    SPIKE = "spike"
    REPAIR = "repair"
    REFACTOR = "refactor"
    PUBLISH_PREPARATION = "publish_preparation"
    INTEGRATION = "integration"
    SETUP = "setup"


class DecompositionComplexity(str, Enum):
    TINY = "tiny"
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    UNKNOWN = "unknown"


class DecompositionOutcome(str, Enum):
    RUNNABLE_PACKET = "runnable_packet"
    DECOMPOSITION_COMPLETED = "decomposition_completed"
    BOOTSTRAP_REQUIRED = "bootstrap_required"
    RUNTIME_PROOF_BLOCKED = "runtime_proof_blocked"
    NEEDS_ENVIRONMENT = "needs_environment"
    BLOCKED_TERMINAL = "blocked_terminal"
    FAILED_TERMINAL = "failed_terminal"
    REPAIR_REQUIRED = "repair_required"
    MANUAL_INTERVENTION_REQUIRED = "manual_intervention_required"




class PacketLocalContract(RuntimeModel):
    environment_nodes: list[str] = Field(default_factory=list)
    work_surfaces: list[str] = Field(default_factory=list)
    verification_levels: list[str] = Field(default_factory=list)
    publish_requirements: list[str] = Field(default_factory=list)

    @field_validator("environment_nodes", "work_surfaces", "verification_levels", "publish_requirements")
    @classmethod
    def _dedupe_contract_lists(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for item in value:
            text = str(item).strip()
            if text and text not in out:
                out.append(text)
        return out


class ExecutionPacket(RuntimeModel):
    packet_id: str
    title: str
    goal: str
    scope: str
    packet_type: ExecutionPacketType
    strategy_id: str | None = None
    status: ExecutionPacketStatus = ExecutionPacketStatus.PENDING
    dependencies: list[str] = Field(default_factory=list)
    allowed_files: list[str] = Field(default_factory=list)
    target_areas: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    metadata: JsonDict = Field(default_factory=dict)
    local_contract: PacketLocalContract = Field(default_factory=PacketLocalContract)

    @field_validator("packet_id", "title", "goal", "scope")
    @classmethod
    def _non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("packet text fields must be non-empty")
        return text

    @field_validator("dependencies", "allowed_files", "target_areas", "forbidden_actions", "success_criteria", "required_evidence")
    @classmethod
    def _dedupe_str_lists(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for item in value:
            text = str(item).strip()
            if text and text not in out:
                out.append(text)
        return out


class DecompositionPlan(RuntimeModel):
    plan_id: str
    task_summary: str
    strategy_id: str | None = None
    complexity: DecompositionComplexity = DecompositionComplexity.UNKNOWN
    packets: list[ExecutionPacket] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    decomposition_reason: str
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    metadata: JsonDict = Field(default_factory=dict)
    local_contract: PacketLocalContract = Field(default_factory=PacketLocalContract)

    @field_validator("plan_id", "task_summary", "decomposition_reason")
    @classmethod
    def _non_empty_plan_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("plan text fields must be non-empty")
        return text


class PacketSelection(RuntimeModel):
    selected_packet_id: str | None = None
    reason: str
    ready: bool = False
    blocked_reason: str | None = None
    pending_dependencies: list[str] = Field(default_factory=list)

    @field_validator("reason")
    @classmethod
    def _non_empty_reason(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("selection reason must be non-empty")
        return text


class DecompositionProgressDecision(RuntimeModel):
    outcome: DecompositionOutcome = DecompositionOutcome.RUNNABLE_PACKET
    current_packet_id: str | None = None
    selected_next_packet_id: str | None = None
    selected_next_stage: str
    plan_completed: bool = False
    terminal: bool = False
    blocked: bool = False
    failed: bool = False
    repair_required: bool = False
    manual_intervention_required: bool = False
    final_status_hint: str | None = None
    blocked_reason: str | None = None
    pending_dependencies: list[str] = Field(default_factory=list)
    reason: str

    @field_validator("selected_next_stage", "reason")
    @classmethod
    def _non_empty_progression_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("progression text fields must be non-empty")
        return text


class PacketHistoryEntry(RuntimeModel):
    packet_id: str
    previous_status: ExecutionPacketStatus | None = None
    new_status: ExecutionPacketStatus
    reason: str
    stage: str
    execution_result_id: str | None = None
    created_at: str = Field(default_factory=utc_now)


class DecompositionValidationResult(RuntimeModel):
    valid: bool
    issues: list[str] = Field(default_factory=list)
    fallback_used: bool = False
    normalized_plan: DecompositionPlan | None = None
