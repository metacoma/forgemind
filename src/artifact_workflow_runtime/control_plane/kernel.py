from __future__ import annotations

from dataclasses import dataclass

from artifact_workflow_runtime.models import Capability, VerificationMode
from artifact_workflow_runtime.models import (
    ApprovalRequest,
    ExecutionPlan,
    ExecutionResult,
    ObservationResult,
    PolicyDecision,
    PublishResult,
    RoutingDecision,
    TaskClassification,
)
from artifact_workflow_runtime.models.state import ControllerDecision
from artifact_workflow_runtime.policy import PolicyEngine
from artifact_workflow_runtime.policy.evidence import EvidenceGate


@dataclass(frozen=True, slots=True)
class VerificationStrategy:
    mode: VerificationMode
    per_check: bool
    requires_world_check: bool
    reason: str


class RuntimeKernel:
    """Controller decision kernel used by the LangGraph runtime.

    LangGraph executes nodes and persists state transitions; this object owns the
    workflow decisions that should not live in OpenHands prompts. OpenHands can
    return hints/evidence, but this kernel decides which workflow edge is taken.
    """

    def __init__(self, *, evidence_gate: EvidenceGate | None = None) -> None:
        self.evidence_gate = evidence_gate or EvidenceGate()

    def controller_decision(self, *, stage: str, selected_next_stage: str, reason: str, required_state_fields: list[str] | None = None, missing_state_fields: list[str] | None = None) -> ControllerDecision:
        return ControllerDecision(
            stage=stage,
            selected_next_stage=selected_next_stage,
            reason=reason,
            required_state_fields=required_state_fields or [],
            missing_state_fields=missing_state_fields or [],
        )

    def next_after_route(self, decision: RoutingDecision) -> str:
        if decision.needs_fresh_external_research:
            return "research"
        if decision.needs_repository_observation or decision.needs_world_observation:
            return "observe"
        return "build_context"

    def next_after_research(self, decision: RoutingDecision) -> str:
        if decision.needs_repository_observation or decision.needs_world_observation:
            return "observe"
        return "build_context"

    def can_plan(self, *, route: RoutingDecision, research: ObservationResult | None, observation: ObservationResult | None) -> tuple[bool, list[str]]:
        missing: list[str] = []
        if route.needs_fresh_external_research and research is None:
            missing.append("research_result")
        if (route.needs_repository_observation or route.needs_world_observation) and observation is None:
            missing.append("observation_result")
        return not missing, missing

    def evaluate_policy(
        self,
        *,
        classification: TaskClassification,
        route: RoutingDecision,
        plan: ExecutionPlan,
        policy_engine: PolicyEngine,
        research: ObservationResult | None,
        observation: ObservationResult | None,
    ) -> PolicyDecision:
        reasons: list[str] = []
        mismatch = _plan_intent_mismatch(classification, plan)
        if mismatch:
            reasons.append(mismatch)
        reasons.extend(self.evidence_gate.evaluate(route=route, plan=plan, research=research, observation=observation))
        if reasons:
            return PolicyDecision(
                allowed=False,
                blocked=True,
                requires_approval=False,
                reasons=reasons,
                execution_family=plan.execution_family,
                capabilities=list(dict.fromkeys([*classification.capabilities, *plan.capabilities])),
            )
        return policy_engine.decide(classification, plan)

    @staticmethod
    def next_after_policy(decision: PolicyDecision | dict[str, object]) -> str:
        blocked = decision.blocked if isinstance(decision, PolicyDecision) else bool(decision.get("blocked"))
        requires_approval = decision.requires_approval if isinstance(decision, PolicyDecision) else bool(decision.get("requires_approval"))
        if blocked:
            return "finalize"
        if requires_approval:
            return "approval"
        return "execute"

    @staticmethod
    def next_after_approval(approval: ApprovalRequest | dict[str, object] | None) -> str:
        if approval is None:
            return "finalize"
        approved = approval.approved if isinstance(approval, ApprovalRequest) else approval.get("approved")
        return "execute" if approved else "finalize"

    @staticmethod
    def next_after_execution(plan: ExecutionPlan, execution: ExecutionResult) -> str:
        if execution.ok and _publish_required(plan):
            return "publish"
        return "verify"

    def verification_strategy(
        self,
        *,
        plan: ExecutionPlan,
        execution: ExecutionResult,
        publish: PublishResult | None = None,
        per_check_routing_enabled: bool = False,
    ) -> VerificationStrategy:
        if not execution.ok:
            return VerificationStrategy(
                mode=VerificationMode.EVIDENCE_REVIEW,
                per_check=False,
                requires_world_check=False,
                reason="Execution failed or returned unusable evidence; controller will use evidence guard verification.",
            )
        verification_text = " ".join([*plan.verification_checks, *plan.required_test_levels, plan.execution_environment]).lower()
        requires_world_check = any(
            marker in verification_text
            for marker in ("world_check", "real_world", "postcheck_in_environment", "cluster live", "host live", "kubectl live", "ansible live", "ssh live")
        ) and publish is None
        if requires_world_check:
            return VerificationStrategy(
                mode=VerificationMode.WORLD_CHECK,
                per_check=False,
                requires_world_check=True,
                reason="Plan declares verification that requires runtime/world access after execution evidence.",
            )
        return VerificationStrategy(
            mode=VerificationMode.EVIDENCE_REVIEW,
            per_check=bool(per_check_routing_enabled and plan.verification_checks),
            requires_world_check=False,
            reason="Verification can be completed as Direct LLM evidence review over artifacts/context.",
        )


_ALLOWED_INTENTS = {"implement", "modify", "investigate", "document", "verify"}


def _effective_task_intent(classification: TaskClassification) -> str:
    intent = (classification.task_intent or "").strip().lower()
    return intent if intent in _ALLOWED_INTENTS else "investigate"


def _plan_intent_mismatch(classification: TaskClassification, plan: ExecutionPlan) -> str | None:
    expected = _effective_task_intent(classification)
    raw_actual = (plan.task_intent or "").strip().lower()
    raw_deliverable = (plan.deliverable_kind or "").strip().lower()
    text = " ".join([plan.summary, *plan.steps, *plan.success_criteria]).lower()
    implementation_markers = ("implement", "add", "modify", "edit", "write code", "create", "update build", "run test", "compile", "fix")
    has_implementation_markers = any(marker in text for marker in implementation_markers)
    actual = raw_actual if raw_actual in _ALLOWED_INTENTS else ""
    if actual in {"", "investigate"} and (plan.requires_mutation or plan.must_change_world or has_implementation_markers):
        actual = "implement"
    deliverable = raw_deliverable
    if deliverable in {"", "analysis"} and (plan.requires_mutation or plan.must_change_world or has_implementation_markers):
        deliverable = "repository_changes" if classification.execution_family.value == "repository_change" else "changes"
    if expected in {"implement", "modify"}:
        if actual not in {"implement", "modify"}:
            return f"Planner degraded a {expected} task into {actual or 'unknown'} intent."
        if deliverable in {"analysis", "documentation"}:
            return f"Planner produced {deliverable} deliverable for a {expected} task instead of real changes."
        if not plan.requires_mutation and not plan.must_change_world and not has_implementation_markers:
            return f"Planner marked a {expected} task as non-mutating, which conflicts with the requested outcome."
        analysis_markers = ("analyze", "design", "document", "outline", "instructions", "review", "draft plan")
        has_analysis = any(marker in text for marker in analysis_markers)
        if has_analysis and not has_implementation_markers:
            return "Planner produced an analysis-only plan for an implementation task."
    return None


def _publish_required(plan: ExecutionPlan) -> bool:
    return bool(plan.require_commit or plan.require_push or Capability.REPO_CREATE_PR in plan.capabilities or plan.publication_steps)
