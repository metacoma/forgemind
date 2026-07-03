from __future__ import annotations

from artifact_workflow_runtime.models import AcceptanceObligationKind, AcceptanceObligationStatus, AcceptanceStatus

from .models import LifecycleEvent, LifecycleFacts, LifecycleStage, LifecycleTransitionDecision
from .policy import OpaPolicyEvaluator


class LifecycleMachine:
    """Strict lifecycle transition engine used by RuntimeKernel.

    LangGraph executes work nodes. This layer owns the lifecycle transition
    decisions and policy gates. It deliberately stays a plain Python transition
    engine: lifecycle legality is determined by typed facts and policy decisions,
    not by framework-specific state declarations.
    """

    def __init__(self, *, policy_evaluator: OpaPolicyEvaluator | None = None) -> None:
        self.policy_evaluator = policy_evaluator or OpaPolicyEvaluator()

    def transition(self, *, from_stage: LifecycleStage, event: LifecycleEvent, facts: LifecycleFacts) -> LifecycleTransitionDecision:
        if event == LifecycleEvent.EXECUTION_FINISHED:
            return self._after_execution(from_stage, facts)
        if event == LifecycleEvent.ACCEPTANCE_EVALUATED:
            return self._after_acceptance(from_stage, facts)
        if event == LifecycleEvent.PUBLISH_FINISHED:
            return self._after_publish(from_stage, facts)
        if event == LifecycleEvent.REPAIR_FINISHED:
            return self._after_repair(from_stage, facts)
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
                allowed=True,
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


    def _after_publish(self, from_stage: LifecycleStage, facts: LifecycleFacts) -> LifecycleTransitionDecision:
        policy = self.policy_evaluator.evaluate("can_leave_publish", facts)
        if not policy.allowed:
            return LifecycleTransitionDecision(
                event=LifecycleEvent.PUBLISH_FINISHED,
                from_stage=from_stage,
                to_stage=LifecycleStage.CONTROL_PLANE_VIOLATION,
                graph_next="finalize",
                allowed=False,
                reason="Publish result violates lifecycle policy; publisher cannot repair/reimplement or expand scope.",
                policy_decision=policy,
                violations=policy.violations,
                facts=facts,
            )
        if facts.publish_failed_checks or facts.publish_has_blockers:
            repair_policy = self.policy_evaluator.evaluate("can_repair", facts)
            if repair_policy.allowed:
                return LifecycleTransitionDecision(
                    event=LifecycleEvent.PUBLISH_FINISHED,
                    from_stage=from_stage,
                    to_stage=LifecycleStage.REPAIRING,
                    graph_next="repair",
                    allowed=True,
                    reason="Publish/PR checks reported failures; lifecycle routes to a bounded repair packet before any new publish attempt.",
                    policy_decision=repair_policy,
                    facts=facts,
                )
            return LifecycleTransitionDecision(
                event=LifecycleEvent.PUBLISH_FINISHED,
                from_stage=from_stage,
                to_stage=LifecycleStage.VERIFYING,
                graph_next="verify",
                allowed=False,
                reason="Publish/PR checks failed but repair policy denied another repair attempt; verification/acceptance must record non-success.",
                policy_decision=repair_policy,
                violations=repair_policy.violations,
                facts=facts,
            )
        return LifecycleTransitionDecision(
            event=LifecycleEvent.PUBLISH_FINISHED,
            from_stage=from_stage,
            to_stage=LifecycleStage.FINALIZING,
            graph_next="finalize",
            allowed=True,
            reason="Publish finished without structured check failures; post-publish verification may finalize and refresh acceptance against publish evidence.",
            policy_decision=policy,
            facts=facts,
        )

    def _after_repair(self, from_stage: LifecycleStage, facts: LifecycleFacts) -> LifecycleTransitionDecision:
        policy = self.policy_evaluator.evaluate("can_leave_execute", facts)
        if not policy.allowed:
            return LifecycleTransitionDecision(
                event=LifecycleEvent.REPAIR_FINISHED,
                from_stage=from_stage,
                to_stage=LifecycleStage.CONTROL_PLANE_VIOLATION,
                graph_next="finalize",
                allowed=False,
                reason="Repair packet violated execute-stage lifecycle policy; workflow cannot continue as normal.",
                policy_decision=policy,
                violations=policy.violations,
                facts=facts,
            )
        return LifecycleTransitionDecision(
            event=LifecycleEvent.REPAIR_FINISHED,
            from_stage=from_stage,
            to_stage=LifecycleStage.EXECUTION_REVIEW,
            graph_next="execution_review",
            allowed=True,
            reason="Repair finished; lifecycle requires another execution review before verification or publish.",
            policy_decision=policy,
            facts=facts,
        )



def _only_publish_obligation_missing(acceptance) -> bool:
    blocking = [item for item in acceptance.obligation_results if item.status != AcceptanceObligationStatus.PASSED]
    return bool(blocking) and all(item.kind == AcceptanceObligationKind.PUBLISH_OBLIGATIONS_SATISFIED for item in blocking)
