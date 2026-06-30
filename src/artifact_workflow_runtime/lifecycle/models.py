from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field

from artifact_workflow_runtime.models import (
    AcceptanceDecision,
    BlockerKind,
    ExecutionPlan,
    ExecutionResult,
    PublishResult,
    RuntimeModel,
    TaskAcceptanceContract,
    VerificationResult,
    utc_now,
)


class LifecycleStage(str, Enum):
    EXECUTING = "executing"
    EXECUTION_REVIEW = "execution_review"
    VERIFYING = "verifying"
    ACCEPTANCE = "acceptance"
    READY_TO_PUBLISH = "ready_to_publish"
    PUBLISHING = "publishing"
    PUBLISH_REVIEW = "publish_review"
    REPAIRING = "repairing"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    NEEDS_ENVIRONMENT = "needs_environment"
    NEEDS_HUMAN_REVIEW = "needs_human_review"
    CONTROL_PLANE_VIOLATION = "control_plane_violation"
    RESEARCH = "research"
    OBSERVE = "observe"
    BUILD_CONTEXT = "build_context"
    OBLIGATIONS = "obligations"
    PLANNING = "planning"
    FAILED = "failed"


class LifecycleEvent(str, Enum):
    EXECUTION_FINISHED = "execution_finished"
    VERIFICATION_FINISHED = "verification_finished"
    ACCEPTANCE_EVALUATED = "acceptance_evaluated"
    PUBLISH_FINISHED = "publish_finished"
    REPAIR_FINISHED = "repair_finished"
    FINALIZE_REQUESTED = "finalize_requested"
    REENTRY_REQUESTED = "reentry_requested"


class PolicyViolation(RuntimeModel):
    code: str
    message: str
    severity: str = "error"
    blocker_kind: BlockerKind | None = None
    evidence_artifact_ids: list[str] = Field(default_factory=list)


class LifecyclePolicyDecision(RuntimeModel):
    allowed: bool
    query: str
    reasons: list[str] = Field(default_factory=list)
    violations: list[PolicyViolation] = Field(default_factory=list)
    engine: str = "fallback"


class PipelineLoopTriggerKind(str, Enum):
    NONE = "none"
    MISSING_RESEARCH_EVIDENCE = "missing_research_evidence"
    MISSING_REPOSITORY_OBSERVATION = "missing_repository_observation"
    MISSING_WORLD_OBSERVATION = "missing_world_observation"
    MISSING_CONTEXT = "missing_context"
    MISSING_OBLIGATIONS = "missing_obligations"
    PLAN_INCOMPLETE = "plan_incomplete"
    VERIFICATION_MISSING_EVIDENCE = "verification_missing_evidence"
    ACCEPTANCE_MISSING_REQUIRED_OBLIGATIONS = "acceptance_missing_required_obligations"
    NEW_SIDE_EFFECTS_DISCOVERED = "new_side_effects_discovered"
    DOCS_IMPACT_DISCOVERED = "docs_impact_discovered"
    EXAMPLES_IMPACT_DISCOVERED = "examples_impact_discovered"
    CI_BUILD_IMPACT_DISCOVERED = "ci_build_impact_discovered"
    CODEGEN_BUILD_IMPACT_DISCOVERED = "codegen_build_impact_discovered"
    INTEGRATION_SCOPE_DISCOVERED = "integration_scope_discovered"
    SETUP_GAP_DISCOVERED = "setup_gap_discovered"
    PUBLISH_DEEPER_PLANNING_REQUIRED = "publish_deeper_planning_required"


class PipelineReentryTarget(str, Enum):
    CONTINUE = "continue"
    RESEARCH = "research"
    OBSERVE = "observe"
    BUILD_CONTEXT = "build_context"
    OBLIGATIONS = "obligations"
    PLAN = "plan"
    FINALIZE = "finalize"


class PipelineLoopBudget(RuntimeModel):
    global_limit: int = 3
    per_trigger_limit: int = 1
    per_source_stage_limit: int = 2


class PipelineLoopDecision(RuntimeModel):
    id: str = Field(default_factory=lambda: f"loop_{__import__('uuid').uuid4().hex[:12]}")
    source_stage: str
    target_stage: PipelineReentryTarget = PipelineReentryTarget.CONTINUE
    trigger_kind: PipelineLoopTriggerKind = PipelineLoopTriggerKind.NONE
    reason: str
    allowed: bool = True
    automatic: bool = False
    missing_evidence: list[str] = Field(default_factory=list)
    missing_obligations: list[str] = Field(default_factory=list)
    blocker_ids: list[str] = Field(default_factory=list)
    loop_count: int = 0
    trigger_count: int = 0
    source_stage_count: int = 0
    global_limit: int = 3
    per_trigger_limit: int = 1
    per_source_stage_limit: int = 2
    budget_exhausted: bool = False
    policy_decision: LifecyclePolicyDecision | None = None
    created_at: str = Field(default_factory=utc_now)


class LifecycleFacts(RuntimeModel):
    """Typed lifecycle facts passed into the FSM/policy layer.

    This is intentionally separate from LangGraph's wire state. LangGraph executes
    nodes; lifecycle facts decide whether a state transition is legal.
    """

    plan: ExecutionPlan | None = None
    acceptance_contract: TaskAcceptanceContract | None = None
    execution: ExecutionResult | None = None
    verification: VerificationResult | None = None
    publish: PublishResult | None = None
    acceptance: AcceptanceDecision | None = None
    publish_required: bool = False
    publish_done: bool = False
    publish_failed_checks: bool = False
    publish_has_blockers: bool = False
    publish_forbidden_action_detected: bool = False
    repair_attempt_count: int = 0
    max_repair_attempts: int = 2
    mutation_task: bool = False
    mandatory_verification_required: bool = False
    mandatory_verification_satisfied: bool = False
    mandatory_verification_blocked: bool = False
    mandatory_verification_missing: bool = False
    environment_blocked: bool = False
    execution_succeeded: bool = False
    execution_blocked: bool = False
    execution_has_blockers: bool = False
    execute_pr_created: bool = False
    execute_git_push: bool = False
    execute_git_commit: bool = False
    execute_forbidden_action_detected: bool = False
    reentry_required: bool = False
    reentry_target_stage: PipelineReentryTarget = PipelineReentryTarget.CONTINUE
    reentry_trigger_kind: PipelineLoopTriggerKind = PipelineLoopTriggerKind.NONE
    reentry_budget_exhausted: bool = False
    pipeline_loop_count: int = 0
    trigger_loop_count: int = 0
    source_stage_loop_count: int = 0
    pipeline_loop_global_limit: int = 3
    pipeline_loop_per_trigger_limit: int = 1
    pipeline_loop_per_source_stage_limit: int = 2
    control_plane_violations: list[PolicyViolation] = Field(default_factory=list)


class LifecycleTransitionDecision(RuntimeModel):
    event: LifecycleEvent
    from_stage: LifecycleStage
    to_stage: LifecycleStage
    graph_next: str
    allowed: bool
    reason: str
    policy_decision: LifecyclePolicyDecision | None = None
    violations: list[PolicyViolation] = Field(default_factory=list)
    facts: LifecycleFacts
