from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, TypedDict

from pydantic import Field

from artifact_workflow_runtime.models.core import (
    ApprovalRequest,
    AcceptanceDecision,
    Artifact,
    ContextPacket,
    ExecutionPlan,
    ExecutionRequest,
    ExecutionResult,
    FinalReport,
    LLMRequest,
    LLMResult,
    ObligationAnalysis,
    ObservationRequest,
    ObservationResult,
    PolicyDecision,
    PublishRequest,
    PublishResult,
    RoutingDecision,
    RuntimeModel,
    Task,
    TaskAcceptanceContract,
    TaskClassification,
    VerificationCheckRequest,
    VerificationCheckResult,
    VerificationRequest,
    VerificationResult,
    utc_now,
)
from artifact_workflow_runtime.lifecycle.models import LifecycleTransitionDecision

JsonDict = dict[str, Any]


class WorkflowStatus(str, Enum):
    CREATED = "created"
    INTAKE_COMPLETED = "intake_completed"
    CLASSIFIED = "classified"
    ROUTED = "routed"
    RESEARCHED = "researched"
    OBSERVED = "observed"
    CONTEXT_BUILT = "context_built"
    OBLIGATIONS_SYNTHESIZED = "obligations_synthesized"
    PLANNED = "planned"
    POLICY_CHECKED = "policy_checked"
    APPROVAL_RESOLVED = "approval_resolved"
    EXECUTED = "executed"
    EXECUTION_REVIEWED = "execution_reviewed"
    PUBLISHED = "published"
    VERIFIED = "verified"
    ACCEPTANCE_EVALUATED = "acceptance_evaluated"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    PARTIALLY_COMPLETED = "partially_completed"
    NEEDS_HUMAN_REVIEW = "needs_human_review"
    NEEDS_ENVIRONMENT = "needs_environment"
    FAILED = "failed"

    @classmethod
    def coerce(cls, value: object) -> "WorkflowStatus":
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            for item in cls:
                if normalized == item.value:
                    return item
        return cls.FAILED


class StageTransition(RuntimeModel):
    from_status: WorkflowStatus | None = None
    to_status: WorkflowStatus
    stage: str
    reason: str
    artifact_ids_added: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)


class ControllerDecision(RuntimeModel):
    stage: str
    selected_next_stage: str
    reason: str
    required_state_fields: list[str] = Field(default_factory=list)
    missing_state_fields: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)


class WorkflowStateSnapshot(RuntimeModel):
    """Typed durable state model for the runtime.

    LangGraph still moves a dict-shaped wire state, but nodes can validate that
    wire state against this model. This keeps the orchestration compatible with
    LangGraph while making the persisted state a typed source of truth instead
    of a loose JsonDict bag.
    """

    task: Task
    task_artifact: Artifact | None = None
    classification_request: LLMRequest | None = None
    classification_result: LLMResult | None = None
    classification: TaskClassification | None = None
    route_request: LLMRequest | None = None
    route_result: LLMResult | None = None
    route_decision: RoutingDecision | None = None
    research_request: ObservationRequest | None = None
    research_result: ObservationResult | None = None
    observation_request: ObservationRequest | None = None
    observation_result: ObservationResult | None = None
    context_packet: ContextPacket | None = None
    obligation_request: LLMRequest | None = None
    obligation_result: LLMResult | None = None
    obligations: ObligationAnalysis | None = None
    plan_request: LLMRequest | None = None
    plan_result: LLMResult | None = None
    plan: ExecutionPlan | None = None
    acceptance_contract: TaskAcceptanceContract | None = None
    policy_decision: PolicyDecision | None = None
    approval_request: ApprovalRequest | None = None
    execution_request: ExecutionRequest | None = None
    execution_result: ExecutionResult | None = None
    execution_review_decision: LifecycleTransitionDecision | None = None
    publish_request: PublishRequest | None = None
    publish_result: PublishResult | None = None
    verification_request: VerificationRequest | None = None
    verification_check_requests: list[VerificationCheckRequest] = Field(default_factory=list)
    verification_check_results: list[VerificationCheckResult] = Field(default_factory=list)
    verification_result: VerificationResult | None = None
    acceptance_decision: AcceptanceDecision | None = None
    final_report: FinalReport | None = None
    lifecycle_decisions: list[LifecycleTransitionDecision] = Field(default_factory=list)
    controller_decisions: list[ControllerDecision] = Field(default_factory=list)
    transitions: list[StageTransition] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    status: WorkflowStatus = WorkflowStatus.CREATED
    errors: list[str] = Field(default_factory=list)

    @classmethod
    def from_graph_state(cls, state: Mapping[str, Any]) -> "WorkflowStateSnapshot":
        data = dict(state)
        if "status" in data:
            data["status"] = WorkflowStatus.coerce(data["status"])
        return cls.model_validate(data)

    def to_graph_state(self) -> dict[str, Any]:
        data = self.model_dump(mode="json", exclude_none=True)
        data["status"] = self.status.value
        return data

    def require(self, *fields: str) -> list[str]:
        missing: list[str] = []
        for field in fields:
            if getattr(self, field, None) is None:
                missing.append(field)
        return missing

    def with_transition(self, *, stage: str, to_status: WorkflowStatus | str, reason: str, artifact_ids_added: list[str] | None = None) -> "WorkflowStateSnapshot":
        target = WorkflowStatus.coerce(to_status)
        transition = StageTransition(
            from_status=self.status,
            to_status=target,
            stage=stage,
            reason=reason,
            artifact_ids_added=artifact_ids_added or [],
        )
        return self.model_copy(update={"status": target, "transitions": [*self.transitions, transition]})


class WorkflowState(TypedDict, total=False):
    # LangGraph wire state. All durable validation should go through
    # WorkflowStateSnapshot.from_graph_state().
    task: JsonDict
    task_artifact: JsonDict | None
    classification_request: JsonDict | None
    classification_result: JsonDict | None
    classification: JsonDict | None
    route_request: JsonDict | None
    route_result: JsonDict | None
    route_decision: JsonDict | None
    research_request: JsonDict | None
    research_result: JsonDict | None
    observation_request: JsonDict | None
    observation_result: JsonDict | None
    context_packet: JsonDict | None
    obligation_request: JsonDict | None
    obligation_result: JsonDict | None
    obligations: JsonDict | None
    plan_request: JsonDict | None
    plan_result: JsonDict | None
    plan: JsonDict | None
    acceptance_contract: JsonDict | None
    policy_decision: JsonDict | None
    approval_request: JsonDict | None
    execution_request: JsonDict | None
    execution_result: JsonDict | None
    execution_review_decision: JsonDict | None
    publish_request: JsonDict | None
    publish_result: JsonDict | None
    verification_request: JsonDict | None
    verification_check_requests: list[JsonDict]
    verification_check_results: list[JsonDict]
    verification_result: JsonDict | None
    acceptance_decision: JsonDict | None
    final_report: JsonDict | None
    lifecycle_decisions: list[JsonDict]
    controller_decisions: list[JsonDict]
    transitions: list[JsonDict]
    artifact_ids: list[str]
    status: str
    errors: list[str]


def validate_workflow_state(state: Mapping[str, Any]) -> WorkflowStateSnapshot:
    return WorkflowStateSnapshot.from_graph_state(state)
