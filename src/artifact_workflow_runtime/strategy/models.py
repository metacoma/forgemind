from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field, field_validator

from artifact_workflow_runtime.models.base import RuntimeModel, utc_now

JsonDict = dict[str, Any]


class StrategyId(str, Enum):
    DEFAULT = "default"
    MVP_FIRST = "mvp_first"
    BDD_INCREMENTAL = "bdd_incremental"
    SPIKE_THEN_HARDEN = "spike_then_harden"
    REPAIR_ONLY = "repair_only"
    SAFE_REFACTOR = "safe_refactor"

    @classmethod
    def coerce(cls, value: object) -> "StrategyId":
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            for item in cls:
                if item.value == normalized:
                    return item
        raise ValueError(f"Unknown strategy id: {value!r}")


class StrategySelectionMode(str, Enum):
    RULE_BASED = "rule_based"
    LLM_ASSISTED = "llm_assisted"
    HYBRID = "hybrid"

    @classmethod
    def coerce(cls, value: object) -> "StrategySelectionMode":
        if value is None:
            return cls.RULE_BASED
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower().replace("-", "_")
            if not normalized:
                return cls.RULE_BASED
            for item in cls:
                if item.value == normalized:
                    return item
        raise ValueError(f"Unknown strategy selection mode: {value!r}")


class StrategyAdvisorStatus(str, Enum):
    SUCCESS = "success"
    DISABLED = "disabled"
    INVALID_JSON = "invalid_json"
    INVALID_STRATEGY = "invalid_strategy"
    BACKEND_ERROR = "backend_error"


class StrategyDefinition(RuntimeModel):
    id: StrategyId
    description: str
    applicable_when: list[str] = Field(default_factory=list)
    packet_stage_preferences: dict[str, list[str]] = Field(default_factory=dict)
    verification_expectations: list[str] = Field(default_factory=list)

    @field_validator("id", mode="before")
    @classmethod
    def _coerce_id(cls, value: object) -> StrategyId:
        return StrategyId.coerce(value)


class StrategyDecision(RuntimeModel):
    selected_strategy: StrategyId
    previous_strategy: StrategyId | None = None
    reason: str
    confidence: str = "medium"
    checkpoint_stage: str
    signals_used: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)

    @field_validator("selected_strategy", mode="before")
    @classmethod
    def _coerce_selected(cls, value: object) -> StrategyId:
        return StrategyId.coerce(value)

    @field_validator("previous_strategy", mode="before")
    @classmethod
    def _coerce_previous(cls, value: object) -> StrategyId | None:
        if value in (None, ""):
            return None
        return StrategyId.coerce(value)

    @field_validator("confidence", mode="before")
    @classmethod
    def _normalize_confidence(cls, value: object) -> str:
        text = str(value or "medium").strip().lower()
        return text if text in {"low", "medium", "high"} else "medium"


class StrategyCheckpointSignals(RuntimeModel):
    current_stage: str
    execution_status: str | None = None
    verification_status: str | None = None
    acceptance_status: str | None = None
    missing_evidence: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    blocker_kinds: list[str] = Field(default_factory=list)
    failed_check_levels: list[str] = Field(default_factory=list)
    explicit_failure_class: str | None = None
    active_packet_type: str | None = None
    active_packet_scope: str | None = None
    repair_count: int = 0
    task_complexity_hint: str = "unknown"
    mutation_heavy: bool = False
    has_tests_obligations: bool = False
    has_docs_obligations: bool = False
    has_ci_obligations: bool = False
    metadata: JsonDict = Field(default_factory=dict)

    @property
    def has_missing_evidence(self) -> bool:
        return bool(self.missing_evidence)

    @property
    def has_blockers(self) -> bool:
        return bool(self.blockers)


class StrategyAdvisorContext(RuntimeModel):
    task_summary: str
    current_stage: str
    allowed_signal_names: list[str] = Field(default_factory=list)
    active_strategy: StrategyId | None = None
    previous_strategy_decisions: list[JsonDict] = Field(default_factory=list)
    available_strategies: list[StrategyDefinition] = Field(default_factory=list)
    checkpoint_signals: StrategyCheckpointSignals
    missing_evidence: list[str] = Field(default_factory=list)
    failed_checks: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    execution_status: str | None = None
    verification_status: str | None = None
    acceptance_status: str | None = None
    repair_count: int = 0
    changed_files_summary: list[str] = Field(default_factory=list)
    known_constraints: list[str] = Field(default_factory=list)
    current_bounded_packet_summary: str | None = None

    @field_validator("active_strategy", mode="before")
    @classmethod
    def _coerce_active(cls, value: object) -> StrategyId | None:
        if value in (None, ""):
            return None
        return StrategyId.coerce(value)


class LLMStrategyRecommendation(RuntimeModel):
    selected_strategy: str | None = None
    reason: str = ""
    confidence: float = 0.0
    signals_used: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    raw_response_artifact_id: str | None = None
    advisor_status: StrategyAdvisorStatus = StrategyAdvisorStatus.SUCCESS
    error: str | None = None

    @field_validator("advisor_status", mode="before")
    @classmethod
    def _coerce_status(cls, value: object) -> StrategyAdvisorStatus:
        if isinstance(value, StrategyAdvisorStatus):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            for item in StrategyAdvisorStatus:
                if item.value == normalized:
                    return item
        return StrategyAdvisorStatus.BACKEND_ERROR

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, value: object) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0


class StrategyValidationResult(RuntimeModel):
    accepted: bool
    final_strategy: StrategyId
    rejection_reason: str | None = None
    fallback_strategy: StrategyId
    policy_notes: list[str] = Field(default_factory=list)

    @field_validator("final_strategy", "fallback_strategy", mode="before")
    @classmethod
    def _coerce_strategy(cls, value: object) -> StrategyId:
        return StrategyId.coerce(value)
