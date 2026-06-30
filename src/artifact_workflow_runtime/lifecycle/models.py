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
)


class LifecycleStage(str, Enum):
    EXECUTING = "executing"
    EXECUTION_REVIEW = "execution_review"
    VERIFYING = "verifying"
    ACCEPTANCE = "acceptance"
    READY_TO_PUBLISH = "ready_to_publish"
    PUBLISHING = "publishing"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    NEEDS_ENVIRONMENT = "needs_environment"
    NEEDS_HUMAN_REVIEW = "needs_human_review"
    CONTROL_PLANE_VIOLATION = "control_plane_violation"
    FAILED = "failed"


class LifecycleEvent(str, Enum):
    EXECUTION_FINISHED = "execution_finished"
    VERIFICATION_FINISHED = "verification_finished"
    ACCEPTANCE_EVALUATED = "acceptance_evaluated"
    PUBLISH_FINISHED = "publish_finished"
    FINALIZE_REQUESTED = "finalize_requested"


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
