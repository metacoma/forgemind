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
