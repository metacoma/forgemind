from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, TypedDict

from pydantic import Field

from artifact_workflow_runtime.done_contract import DoneContract
from artifact_workflow_runtime.environment import EnvironmentPlan
from artifact_workflow_runtime.qa import QAExecutionReport, QAPlan, QAReview

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
    RepairRequest,
    RepairResult,
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
from artifact_workflow_runtime.lifecycle.models import LifecycleTransitionDecision, PipelineLoopDecision
from artifact_workflow_runtime.strategy.models import StrategyDecision, StrategyId
from artifact_workflow_runtime.decomposition.models import DecompositionPlan, DecompositionProgressDecision, PacketHistoryEntry
from artifact_workflow_runtime.freshness.models import FreshnessDecision, RetrievalSnapshot

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
    DONE_CONTRACT_BUILT = "done_contract_built"
    PLANNED = "planned"
    WORKSPACE_PREPARED = "workspace_prepared"
    POLICY_CHECKED = "policy_checked"
    APPROVAL_RESOLVED = "approval_resolved"
    EXECUTED = "executed"
    REVIEWED = "reviewed"
    QA_PLANNED = "qa_planned"
    QA_EXECUTED = "qa_executed"
    QA_REVIEWED = "qa_reviewed"
    EXECUTION_REVIEWED = "execution_reviewed"
    REPAIRED = "repaired"
    PUBLISH_REVIEWED = "publish_reviewed"
    PUBLISHED = "published"
    POST_PUBLISH_VERIFIED = "post_publish_verified"
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




class CoreWorkflowStage(str, Enum):
    INTAKE = "intake"
    CLASSIFY = "classify"
    ROUTE = "route"
    RESEARCH = "research"
    OBSERVE = "observe"
    BUILD_CONTEXT = "build_context"
    OBLIGATIONS = "obligations"
    DONE_CONTRACT = "done_contract"
    PLAN = "plan"
    POLICY = "policy"
    APPROVAL = "approval"
    WORKSPACE_PREPARE = "workspace_prepare"
    EXECUTE = "execute"
    REVIEW = "review"
    QA_PLAN = "qa_plan"
    QA_EXECUTE = "qa_execute"
    QA_REVIEW = "qa_review"
    VERIFY = "verify"
    ACCEPTANCE = "acceptance"
    PUBLISH = "publish"
    POST_PUBLISH_VERIFY = "post_publish_verify"
    REPAIR = "repair"
    FINALIZE = "finalize"

    @classmethod
    def coerce(cls, value: object) -> "CoreWorkflowStage":
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            for item in cls:
                if item.value == normalized:
                    return item
        raise ValueError(f"Unknown workflow stage: {value!r}")


class StageStateContract(RuntimeModel):
    """Typed boundary contract for a graph/controller stage.

    Stage nodes may still exchange LangGraph-compatible dictionaries, but every
    stage boundary has one canonical list of required input fields and expected
    output fields. This keeps readiness checks out of free-form prompts and
    prevents graph/controller/policy layers from silently diverging.
    """

    stage: CoreWorkflowStage
    required_fields: tuple[str, ...] = ()
    produced_fields: tuple[str, ...] = ()
    status_after: WorkflowStatus | None = None


def _stage_contract(stage: CoreWorkflowStage | str, required: tuple[str, ...], produced: tuple[str, ...], status: WorkflowStatus | None) -> StageStateContract:
    return StageStateContract(stage=CoreWorkflowStage.coerce(stage), required_fields=required, produced_fields=produced, status_after=status)


STAGE_STATE_CONTRACTS: dict[CoreWorkflowStage, StageStateContract] = {
    contract.stage: contract
    for contract in (
        _stage_contract(CoreWorkflowStage.INTAKE, ("task",), ("task_artifact",), WorkflowStatus.INTAKE_COMPLETED),
        _stage_contract(CoreWorkflowStage.CLASSIFY, ("task",), ("classification_request", "classification_result", "classification"), WorkflowStatus.CLASSIFIED),
        _stage_contract(CoreWorkflowStage.ROUTE, ("task", "classification"), ("route_request", "route_result", "route_decision"), WorkflowStatus.ROUTED),
        _stage_contract(CoreWorkflowStage.RESEARCH, ("task", "route_decision"), ("research_request", "research_result"), WorkflowStatus.RESEARCHED),
        _stage_contract(CoreWorkflowStage.OBSERVE, ("task", "route_decision"), ("observation_request", "observation_result"), WorkflowStatus.OBSERVED),
        _stage_contract(CoreWorkflowStage.BUILD_CONTEXT, ("task",), ("context_packet",), WorkflowStatus.CONTEXT_BUILT),
        _stage_contract(CoreWorkflowStage.OBLIGATIONS, ("task", "classification", "route_decision", "context_packet"), ("obligation_request", "obligation_result", "obligations"), WorkflowStatus.OBLIGATIONS_SYNTHESIZED),
        _stage_contract(CoreWorkflowStage.DONE_CONTRACT, ("task", "classification", "obligations"), ("done_contract",), WorkflowStatus.DONE_CONTRACT_BUILT),
        _stage_contract(CoreWorkflowStage.PLAN, ("task", "classification", "context_packet", "obligations", "done_contract"), ("plan_request", "plan_result", "plan", "acceptance_contract", "decomposition_plan", "active_packet_id"), WorkflowStatus.PLANNED),
        _stage_contract(CoreWorkflowStage.POLICY, ("task", "classification", "route_decision", "plan"), ("policy_decision",), WorkflowStatus.POLICY_CHECKED),
        _stage_contract(CoreWorkflowStage.APPROVAL, ("policy_decision",), ("approval_request",), WorkflowStatus.APPROVAL_RESOLVED),
        _stage_contract(CoreWorkflowStage.WORKSPACE_PREPARE, ("task", "done_contract"), ("workspace_branch", "workspace_root", "environment_plan"), WorkflowStatus.WORKSPACE_PREPARED),
        _stage_contract(CoreWorkflowStage.EXECUTE, ("task", "plan", "context_packet"), ("execution_request", "execution_result"), WorkflowStatus.EXECUTED),
        _stage_contract(CoreWorkflowStage.REVIEW, ("task", "plan", "execution_result", "done_contract"), ("review_result", "execution_review_decision"), WorkflowStatus.REVIEWED),
        _stage_contract(CoreWorkflowStage.QA_PLAN, ("task", "plan", "done_contract"), ("qa_plan",), WorkflowStatus.QA_PLANNED),
        _stage_contract(CoreWorkflowStage.QA_EXECUTE, ("task", "qa_plan", "workspace_root"), ("qa_execution_report",), WorkflowStatus.QA_EXECUTED),
        _stage_contract(CoreWorkflowStage.QA_REVIEW, ("task", "plan", "execution_result", "context_packet", "qa_execution_report"), ("verification_request", "verification_result", "qa_review_result"), WorkflowStatus.QA_REVIEWED),
        _stage_contract(CoreWorkflowStage.VERIFY, ("task", "plan", "execution_result", "context_packet"), ("verification_request", "verification_result"), WorkflowStatus.VERIFIED),
        _stage_contract(CoreWorkflowStage.ACCEPTANCE, ("task", "acceptance_contract"), ("acceptance_decision",), WorkflowStatus.ACCEPTANCE_EVALUATED),
        _stage_contract(CoreWorkflowStage.PUBLISH, ("task", "plan", "execution_result", "done_contract", "acceptance_decision"), ("publish_request", "publish_result"), WorkflowStatus.PUBLISHED),
        _stage_contract(CoreWorkflowStage.POST_PUBLISH_VERIFY, ("task", "plan", "publish_result"), ("publish_review_decision",), WorkflowStatus.POST_PUBLISH_VERIFIED),
        _stage_contract(CoreWorkflowStage.REPAIR, ("task", "plan", "execution_result"), ("repair_requests", "repair_results", "execution_result"), WorkflowStatus.REPAIRED),
        _stage_contract(CoreWorkflowStage.FINALIZE, ("task",), ("final_report",), None),
    )
}


STATUS_REQUIRED_FIELDS: dict[WorkflowStatus, tuple[str, ...]] = {
    WorkflowStatus.CREATED: ("task",),
    WorkflowStatus.INTAKE_COMPLETED: ("task", "task_artifact"),
    WorkflowStatus.CLASSIFIED: ("task", "classification"),
    WorkflowStatus.ROUTED: ("task", "classification", "route_decision"),
    WorkflowStatus.RESEARCHED: ("task", "research_result"),
    WorkflowStatus.OBSERVED: ("task", "observation_result"),
    WorkflowStatus.CONTEXT_BUILT: ("task", "context_packet"),
    WorkflowStatus.OBLIGATIONS_SYNTHESIZED: ("task", "obligations"),
    WorkflowStatus.DONE_CONTRACT_BUILT: ("task", "done_contract"),
    WorkflowStatus.PLANNED: ("task", "plan", "acceptance_contract", "decomposition_plan"),
    WorkflowStatus.POLICY_CHECKED: ("task", "plan", "policy_decision"),
    WorkflowStatus.APPROVAL_RESOLVED: ("task", "policy_decision", "approval_request"),
    WorkflowStatus.WORKSPACE_PREPARED: ("task", "workspace_branch", "workspace_root", "environment_plan"),
    WorkflowStatus.EXECUTED: ("task", "plan", "execution_result"),
    WorkflowStatus.REVIEWED: ("task", "execution_result", "review_result", "execution_review_decision"),
    WorkflowStatus.QA_PLANNED: ("task", "qa_plan"),
    WorkflowStatus.QA_EXECUTED: ("task", "workspace_root", "qa_execution_report"),
    WorkflowStatus.QA_REVIEWED: ("task", "qa_review_result"),
    WorkflowStatus.EXECUTION_REVIEWED: ("task", "execution_result", "execution_review_decision"),
    WorkflowStatus.REPAIRED: ("task", "execution_result", "repair_results"),
    WorkflowStatus.PUBLISH_REVIEWED: ("task", "publish_result", "publish_review_decision"),
    WorkflowStatus.PUBLISHED: ("task", "publish_result"),
    WorkflowStatus.POST_PUBLISH_VERIFIED: ("task", "publish_result", "publish_review_decision"),
    WorkflowStatus.VERIFIED: ("task", "verification_result"),
    WorkflowStatus.ACCEPTANCE_EVALUATED: ("task", "acceptance_decision"),
    WorkflowStatus.COMPLETED: ("task", "final_report"),
    WorkflowStatus.BLOCKED: ("task", "final_report"),
    WorkflowStatus.PARTIALLY_COMPLETED: ("task", "final_report"),
    WorkflowStatus.NEEDS_HUMAN_REVIEW: ("task", "final_report"),
    WorkflowStatus.NEEDS_ENVIRONMENT: ("task", "final_report"),
    WorkflowStatus.FAILED: ("task", "final_report"),
}


TERMINAL_WORKFLOW_STATUSES: frozenset[WorkflowStatus] = frozenset({
    WorkflowStatus.COMPLETED,
    WorkflowStatus.BLOCKED,
    WorkflowStatus.PARTIALLY_COMPLETED,
    WorkflowStatus.NEEDS_HUMAN_REVIEW,
    WorkflowStatus.NEEDS_ENVIRONMENT,
    WorkflowStatus.FAILED,
})


def stage_state_contract(stage: CoreWorkflowStage | str) -> StageStateContract:
    stage_name = CoreWorkflowStage.coerce(stage)
    try:
        return STAGE_STATE_CONTRACTS[stage_name]
    except KeyError as exc:  # pragma: no cover - enum coverage should prevent this
        raise ValueError(f"No state contract registered for stage {stage_name.value!r}") from exc


def required_fields_for_stage(stage: CoreWorkflowStage | str) -> tuple[str, ...]:
    return stage_state_contract(stage).required_fields


def produced_fields_for_stage(stage: CoreWorkflowStage | str) -> tuple[str, ...]:
    return stage_state_contract(stage).produced_fields


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
    freshness_decision: FreshnessDecision | None = None
    retrieval_snapshot: RetrievalSnapshot | None = None
    retrieval_artifact_ids: list[str] = Field(default_factory=list)
    observation_request: ObservationRequest | None = None
    observation_result: ObservationResult | None = None
    context_packet: ContextPacket | None = None
    obligation_request: LLMRequest | None = None
    obligation_result: LLMResult | None = None
    obligations: ObligationAnalysis | None = None
    done_contract: DoneContract | None = None
    workspace_branch: str | None = None
    workspace_root: str | None = None
    environment_plan: EnvironmentPlan | None = None
    plan_request: LLMRequest | None = None
    plan_result: LLMResult | None = None
    plan: ExecutionPlan | None = None
    acceptance_contract: TaskAcceptanceContract | None = None
    policy_decision: PolicyDecision | None = None
    approval_request: ApprovalRequest | None = None
    execution_request: ExecutionRequest | None = None
    execution_result: ExecutionResult | None = None
    review_result: QAReview | None = None
    qa_plan: QAPlan | None = None
    qa_execution_report: QAExecutionReport | None = None
    qa_review_result: QAReview | None = None
    execution_review_decision: LifecycleTransitionDecision | None = None
    repair_requests: list[RepairRequest] = Field(default_factory=list)
    repair_results: list[RepairResult] = Field(default_factory=list)
    publish_request: PublishRequest | None = None
    publish_result: PublishResult | None = None
    publish_review_decision: LifecycleTransitionDecision | None = None
    verification_request: VerificationRequest | None = None
    verification_check_requests: list[VerificationCheckRequest] = Field(default_factory=list)
    verification_check_results: list[VerificationCheckResult] = Field(default_factory=list)
    verification_result: VerificationResult | None = None
    acceptance_decision: AcceptanceDecision | None = None
    final_report: FinalReport | None = None
    lifecycle_decisions: list[LifecycleTransitionDecision] = Field(default_factory=list)
    pipeline_loop_decisions: list[PipelineLoopDecision] = Field(default_factory=list)
    controller_decisions: list[ControllerDecision] = Field(default_factory=list)
    active_strategy: StrategyId | None = None
    strategy_decisions: list[StrategyDecision] = Field(default_factory=list)
    decomposition_plan: DecompositionPlan | None = None
    active_packet_id: str | None = None
    packet_history: list[PacketHistoryEntry] = Field(default_factory=list)
    packet_progression: DecompositionProgressDecision | None = None
    resume_next_stage: str | None = None
    resume_checkpoint_id: str | None = None
    recovered_from_checkpoint: bool = False
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

    def missing_for_stage(self, stage: CoreWorkflowStage | str, *additional_fields: str) -> list[str]:
        fields = [*required_fields_for_stage(stage), *additional_fields]
        return self.require(*_unique_field_names(fields))

    def assert_ready_for_stage(self, stage: CoreWorkflowStage | str, *additional_fields: str) -> None:
        missing = self.missing_for_stage(stage, *additional_fields)
        if missing:
            raise ValueError(f"Stage {CoreWorkflowStage.coerce(stage).value!r} is not ready; missing state fields: {missing}")

    def core_invariant_errors(self, *, final: bool = False) -> list[str]:
        """Return deterministic typed-state invariant violations.

        This is deliberately conservative: it checks the current status, final
        report alignment, transition/artifact bookkeeping, and the stage output
        fields that are explicitly claimed by transitions. It does not try to
        infer business success from prose.
        """

        errors: list[str] = []
        required = STATUS_REQUIRED_FIELDS.get(self.status, ("task",))
        missing = self.require(*required)
        if missing:
            errors.append(f"status {self.status.value!r} requires fields {missing}")

        if self.transitions:
            last = self.transitions[-1]
            if last.to_status != self.status:
                errors.append(f"last transition to_status {last.to_status.value!r} does not match state status {self.status.value!r}")
            for transition in self.transitions:
                for artifact_id in transition.artifact_ids_added:
                    if artifact_id not in self.artifact_ids:
                        errors.append(f"transition {transition.stage!r} references artifact {artifact_id!r} missing from artifact_ids")
        elif self.status != WorkflowStatus.CREATED:
            errors.append(f"non-created status {self.status.value!r} has no transition history")

        if self.final_report is not None and WorkflowStatus.coerce(self.final_report.status) != self.status:
            errors.append(f"final_report.status {self.final_report.status!r} does not coerce to workflow status {self.status.value!r}")

        if final and self.status in TERMINAL_WORKFLOW_STATUSES and self.final_report is None:
            errors.append(f"terminal status {self.status.value!r} requires final_report")

        return _unique_field_names(errors)

    def assert_core_invariants(self, *, final: bool = False) -> None:
        errors = self.core_invariant_errors(final=final)
        if errors:
            raise ValueError("WorkflowStateSnapshot invariant violation: " + "; ".join(errors))

    def with_transition(self, *, stage: str, to_status: WorkflowStatus | str, reason: str, artifact_ids_added: list[str] | None = None) -> "WorkflowStateSnapshot":
        target = WorkflowStatus.coerce(to_status)
        added = list(artifact_ids_added or [])
        transition = StageTransition(
            from_status=self.status,
            to_status=target,
            stage=stage,
            reason=reason,
            artifact_ids_added=added,
        )
        artifact_ids = [*self.artifact_ids]
        for artifact_id in added:
            if artifact_id not in artifact_ids:
                artifact_ids.append(artifact_id)
        return self.model_copy(update={"status": target, "artifact_ids": artifact_ids, "transitions": [*self.transitions, transition]})


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
    freshness_decision: JsonDict | None
    retrieval_snapshot: JsonDict | None
    retrieval_artifact_ids: list[str]
    observation_request: JsonDict | None
    observation_result: JsonDict | None
    context_packet: JsonDict | None
    obligation_request: JsonDict | None
    obligation_result: JsonDict | None
    obligations: JsonDict | None
    done_contract: JsonDict | None
    workspace_branch: str | None
    workspace_root: str | None
    environment_plan: JsonDict | None
    plan_request: JsonDict | None
    plan_result: JsonDict | None
    plan: JsonDict | None
    acceptance_contract: JsonDict | None
    policy_decision: JsonDict | None
    approval_request: JsonDict | None
    execution_request: JsonDict | None
    execution_result: JsonDict | None
    review_result: JsonDict | None
    qa_plan: JsonDict | None
    qa_execution_report: JsonDict | None
    qa_review_result: JsonDict | None
    execution_review_decision: JsonDict | None
    repair_requests: list[JsonDict]
    repair_results: list[JsonDict]
    publish_request: JsonDict | None
    publish_result: JsonDict | None
    publish_review_decision: JsonDict | None
    verification_request: JsonDict | None
    verification_check_requests: list[JsonDict]
    verification_check_results: list[JsonDict]
    verification_result: JsonDict | None
    acceptance_decision: JsonDict | None
    final_report: JsonDict | None
    lifecycle_decisions: list[JsonDict]
    pipeline_loop_decisions: list[JsonDict]
    controller_decisions: list[JsonDict]
    active_strategy: str | None
    strategy_decisions: list[JsonDict]
    decomposition_plan: JsonDict | None
    active_packet_id: str | None
    packet_history: list[JsonDict]
    packet_progression: JsonDict | None
    resume_next_stage: str | None
    resume_checkpoint_id: str | None
    recovered_from_checkpoint: bool
    transitions: list[JsonDict]
    artifact_ids: list[str]
    status: str
    errors: list[str]


def _unique_field_names(items: list[str] | tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return out


def validate_workflow_state(state: Mapping[str, Any], *, final: bool = False) -> WorkflowStateSnapshot:
    snapshot = WorkflowStateSnapshot.from_graph_state(state)
    snapshot.assert_core_invariants(final=final)
    return snapshot
