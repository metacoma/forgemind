from __future__ import annotations

try:  # optional runtime dependency; fallback keeps tests/self-contained tarballs runnable.
    from statemachine import StateMachine as _ExternalStateMachine  # type: ignore
except Exception:  # pragma: no cover - depends on optional dependency availability
    _ExternalStateMachine = object  # type: ignore

from artifact_workflow_runtime.models import AcceptanceObligationKind, AcceptanceObligationStatus, AcceptanceStatus

from .models import LifecycleEvent, LifecycleFacts, LifecycleStage, LifecycleTransitionDecision
from .policy import OpaPolicyEvaluator


class LifecycleMachine(_ExternalStateMachine):  # type: ignore[misc]
    """Strict lifecycle transition engine used by RuntimeKernel.

    The class is intentionally small: LangGraph still executes work nodes, while
    this layer decides whether transitions such as execute->publish or
    acceptance->finalize are legal under typed facts and OPA/Rego policy gates.
    """

    def __init__(self, *, policy_evaluator: OpaPolicyEvaluator | None = None) -> None:
        # External python-statemachine may define its own __init__; object does not need it.
        try:
            super().__init__()  # type: ignore[misc]
        except TypeError:
            pass
        self.policy_evaluator = policy_evaluator or OpaPolicyEvaluator()

    def transition(self, *, from_stage: LifecycleStage, event: LifecycleEvent, facts: LifecycleFacts) -> LifecycleTransitionDecision:
        if event == LifecycleEvent.EXECUTION_FINISHED:
            return self._after_execution(from_stage, facts)
        if event == LifecycleEvent.ACCEPTANCE_EVALUATED:
            return self._after_acceptance(from_stage, facts)
        if event == LifecycleEvent.PUBLISH_FINISHED:
            return LifecycleTransitionDecision(
                event=event,
                from_stage=from_stage,
                to_stage=LifecycleStage.VERIFYING,
                graph_next="verify",
                allowed=True,
                reason="Publish finished; post-publish verification must review PR/commit/check evidence.",
                facts=facts,
            )
        return LifecycleTransitionDecision(
            event=event,
            from_stage=from_stage,
            to_stage=LifecycleStage.FINALIZING,
            graph_next="finalize",
            allowed=True,
            reason="Lifecycle event defaults to finalization.",
            facts=facts,
        )

    def _after_execution(self, from_stage: LifecycleStage, facts: LifecycleFacts) -> LifecycleTransitionDecision:
        policy = self.policy_evaluator.evaluate("can_leave_execute", facts)
        if not policy.allowed:
            return LifecycleTransitionDecision(
                event=LifecycleEvent.EXECUTION_FINISHED,
                from_stage=from_stage,
                to_stage=LifecycleStage.CONTROL_PLANE_VIOLATION,
                graph_next="finalize",
                allowed=False,
                reason="Execution result violates lifecycle policy; workflow cannot continue as normal.",
                policy_decision=policy,
                violations=policy.violations,
                facts=facts,
            )
        if facts.environment_blocked:
            return LifecycleTransitionDecision(
                event=LifecycleEvent.EXECUTION_FINISHED,
                from_stage=from_stage,
                to_stage=LifecycleStage.VERIFYING,
                graph_next="verify",
                allowed=True,
                reason="Execution reported an environment blocker; verification records blocked obligations before acceptance. Publish remains forbidden.",
                policy_decision=policy,
                facts=facts,
            )
        if facts.mandatory_verification_required and not facts.mandatory_verification_satisfied:
            return LifecycleTransitionDecision(
                event=LifecycleEvent.EXECUTION_FINISHED,
                from_stage=from_stage,
                to_stage=LifecycleStage.VERIFYING,
                graph_next="verify",
                allowed=True,
                reason="Mutation task requires mandatory verification before publish/finalization.",
                policy_decision=policy,
                facts=facts,
            )
        if facts.publish_required:
            publish_policy = self.policy_evaluator.evaluate("can_publish", facts)
            if publish_policy.allowed:
                return LifecycleTransitionDecision(
                    event=LifecycleEvent.EXECUTION_FINISHED,
                    from_stage=from_stage,
                    to_stage=LifecycleStage.READY_TO_PUBLISH,
                    graph_next="publish",
                    allowed=True,
                    reason="Execution is clean and publish policy allows repository publication.",
                    policy_decision=publish_policy,
                    facts=facts,
                )
            return LifecycleTransitionDecision(
                event=LifecycleEvent.EXECUTION_FINISHED,
                from_stage=from_stage,
                to_stage=LifecycleStage.VERIFYING,
                graph_next="verify",
                allowed=False,
                reason="Publish policy denied direct publish; verification/acceptance must resolve blockers first.",
                policy_decision=publish_policy,
                violations=publish_policy.violations,
                facts=facts,
            )
        return LifecycleTransitionDecision(
            event=LifecycleEvent.EXECUTION_FINISHED,
            from_stage=from_stage,
            to_stage=LifecycleStage.VERIFYING,
            graph_next="verify",
            allowed=True,
            reason="No publish is required; continue to verification.",
            policy_decision=policy,
            facts=facts,
        )

    def _after_acceptance(self, from_stage: LifecycleStage, facts: LifecycleFacts) -> LifecycleTransitionDecision:
        acceptance = facts.acceptance
        if acceptance is None:
            return LifecycleTransitionDecision(
                event=LifecycleEvent.ACCEPTANCE_EVALUATED,
                from_stage=from_stage,
                to_stage=LifecycleStage.NEEDS_HUMAN_REVIEW,
                graph_next="finalize",
                allowed=False,
                reason="Acceptance decision is missing; lifecycle cannot continue.",
                facts=facts,
            )
        publish_gate_ready = acceptance.accepted or _only_publish_obligation_missing(acceptance)
        if facts.publish_required and not facts.publish_done and facts.mandatory_verification_satisfied and publish_gate_ready:
            publish_policy = self.policy_evaluator.evaluate("can_publish", facts)
            if publish_policy.allowed:
                return LifecycleTransitionDecision(
                    event=LifecycleEvent.ACCEPTANCE_EVALUATED,
                    from_stage=from_stage,
                    to_stage=LifecycleStage.READY_TO_PUBLISH,
                    graph_next="publish",
                    allowed=True,
                    reason="Pre-publish obligations are satisfied; repository publication may run as a bounded publish packet.",
                    policy_decision=publish_policy,
                    facts=facts,
                )
        return LifecycleTransitionDecision(
            event=LifecycleEvent.ACCEPTANCE_EVALUATED,
            from_stage=from_stage,
            to_stage=LifecycleStage.FINALIZING,
            graph_next="finalize",
            allowed=acceptance.accepted,
            reason=f"Acceptance resolved as {acceptance.status.value}; finalization is the only legal next step.",
            facts=facts,
        )



def _only_publish_obligation_missing(acceptance) -> bool:
    blocking = [item for item in acceptance.obligation_results if item.status != AcceptanceObligationStatus.PASSED]
    return bool(blocking) and all(item.kind == AcceptanceObligationKind.PUBLISH_OBLIGATIONS_SATISFIED for item in blocking)
