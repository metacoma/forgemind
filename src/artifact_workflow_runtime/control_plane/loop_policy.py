from __future__ import annotations

from typing import Iterable

from artifact_workflow_runtime.lifecycle import (
    LifecycleFacts,
    LifecyclePolicyDecision,
    OpaPolicyEvaluator,
    PipelineLoopBudget,
    PipelineLoopDecision,
    PipelineLoopTrigger,
    PipelineLoopTriggerKind,
    PipelineReentryTarget,
    LoopTerminalOutcome,
)
from artifact_workflow_runtime.models import (
    AcceptanceDecision,
    AcceptanceObligationStatus,
    DiscoveredImpactKind,
    ExecutionPlan,
    ObligationAnalysis,
    PublishResult,
    VerificationResult,
)


class PipelineLoopPolicy:
    """Centralized typed pipeline-wide re-entry controller.

    The policy is intentionally deterministic and typed-first. It uses discovered
    impacts, evidence gaps, acceptance blockers, and plan/runtime semantics
    before falling back to legacy text markers.
    """

    def __init__(self, *, policy_evaluator: OpaPolicyEvaluator | None = None) -> None:
        self.policy_evaluator = policy_evaluator or OpaPolicyEvaluator()

    def evaluate(
        self,
        *,
        source_stage: str,
        plan: ExecutionPlan | None = None,
        obligations: ObligationAnalysis | None = None,
        verification: VerificationResult | None = None,
        acceptance: AcceptanceDecision | None = None,
        publish: PublishResult | None = None,
        loop_decisions: list[PipelineLoopDecision | dict[str, object]] | None = None,
        budget: PipelineLoopBudget | None = None,
    ) -> PipelineLoopDecision:
        budget = budget or PipelineLoopBudget()
        prior = [_coerce_loop_decision(item) for item in (loop_decisions or [])]
        trigger = self.detect_trigger(
            source_stage=source_stage,
            plan=plan,
            obligations=obligations,
            verification=verification,
            acceptance=acceptance,
            publish=publish,
        )
        if trigger.kind == PipelineLoopTriggerKind.NONE:
            return PipelineLoopDecision(
                source_stage=source_stage,
                target_stage=PipelineReentryTarget.CONTINUE,
                trigger_kind=trigger.kind,
                trigger=trigger,
                reason="No pipeline-wide re-entry trigger detected.",
                automatic=False,
                allowed=True,
                loop_count=len(prior),
                global_limit=budget.global_limit,
                per_trigger_limit=budget.per_trigger_limit,
                per_source_stage_limit=budget.per_source_stage_limit,
                per_target_limit=budget.per_target_limit,
            )

        target = self._target_for_trigger(trigger.kind)
        trigger_count = sum(1 for item in prior if item.trigger_kind == trigger.kind)
        source_count = sum(1 for item in prior if item.source_stage == source_stage)
        target_count = sum(1 for item in prior if item.target_stage == target)
        exhausted = (
            len(prior) >= budget.global_limit
            or trigger_count >= budget.per_trigger_limit
            or source_count >= budget.per_source_stage_limit
            or target_count >= budget.per_target_limit
        )
        terminal_outcome = LoopTerminalOutcome.LOOP_EXHAUSTED if exhausted else LoopTerminalOutcome.NONE
        facts = LifecycleFacts(
            plan=plan,
            verification=verification,
            publish=publish,
            acceptance=acceptance,
            reentry_required=True,
            reentry_target_stage=target,
            reentry_trigger_kind=trigger.kind,
            reentry_budget_exhausted=exhausted,
            pipeline_loop_count=len(prior),
            trigger_loop_count=trigger_count,
            source_stage_loop_count=source_count,
            target_stage_loop_count=target_count,
            pipeline_loop_global_limit=budget.global_limit,
            pipeline_loop_per_trigger_limit=budget.per_trigger_limit,
            pipeline_loop_per_source_stage_limit=budget.per_source_stage_limit,
            pipeline_loop_per_target_limit=budget.per_target_limit,
        )
        policy = self.policy_evaluator.evaluate("can_reenter", facts)
        allowed = policy.allowed and not exhausted
        return PipelineLoopDecision(
            source_stage=source_stage,
            target_stage=target if allowed else PipelineReentryTarget.FINALIZE,
            trigger_kind=trigger.kind,
            trigger=trigger,
            reason=trigger.reason if allowed else f"Pipeline re-entry requested for {trigger.kind.value}, but loop budget/policy denied it.",
            allowed=allowed,
            automatic=allowed,
            missing_evidence=list(trigger.missing_evidence),
            missing_obligations=list(trigger.missing_obligations),
            blocker_ids=list(trigger.blocker_ids),
            loop_count=len(prior),
            trigger_count=trigger_count,
            source_stage_count=source_count,
            target_stage_count=target_count,
            global_limit=budget.global_limit,
            per_trigger_limit=budget.per_trigger_limit,
            per_source_stage_limit=budget.per_source_stage_limit,
            per_target_limit=budget.per_target_limit,
            budget_exhausted=exhausted,
            terminal_outcome=terminal_outcome,
            policy_decision=policy,
        )

    def detect_trigger(
        self,
        *,
        source_stage: str,
        plan: ExecutionPlan | None,
        obligations: ObligationAnalysis | None,
        verification: VerificationResult | None,
        acceptance: AcceptanceDecision | None,
        publish: PublishResult | None,
    ) -> PipelineLoopTrigger:
        missing_evidence: list[str] = []
        missing_obligations: list[str] = []
        blocker_ids: list[str] = []
        discovered_impacts: list[str] = []
        blocker_summaries: list[str] = []
        environment_gaps: list[str] = []

        if verification is not None:
            missing_evidence.extend(verification.missing_evidence)
            missing_obligations.extend(verification.missing_obligations)
            missing_obligations.extend(verification.missing_test_levels)
            environment_gaps.extend(verification.missing_setup_steps)
            blocker_summaries.extend(item.summary for item in verification.structured_evidence.blockers)
        if acceptance is not None:
            for result in acceptance.obligation_results:
                if result.status != AcceptanceObligationStatus.PASSED:
                    missing_obligations.append(result.obligation_name)
            blocker_summaries.extend(item.reason for item in acceptance.obligation_results if item.status != AcceptanceObligationStatus.PASSED)
        if publish is not None:
            blocker_summaries.extend(item.summary for item in publish.structured_evidence.blockers)
        if obligations is not None:
            blocker_summaries.extend(obligations.blocker_conditions)
            for impact in obligations.discovered_impacts:
                discovered_impacts.append(impact.kind.value)
                if impact.blocking:
                    blocker_summaries.append(impact.summary)
                if impact.kind in {DiscoveredImpactKind.SETUP, DiscoveredImpactKind.OBSERVATION}:
                    environment_gaps.append(impact.summary)

        for impact in list(obligations.discovered_impacts) if obligations is not None else []:
            if impact.kind == DiscoveredImpactKind.RESEARCH:
                return PipelineLoopTrigger(
                    kind=PipelineLoopTriggerKind.MISSING_RESEARCH_EVIDENCE,
                    reason="New external/current documentation evidence is required before replanning.",
                    missing_evidence=_dedupe(missing_evidence),
                    missing_obligations=_dedupe(missing_obligations),
                    blocker_ids=blocker_ids,
                    discovered_impacts=[impact.kind.value],
                    blocker_summaries=[impact.summary],
                    environment_gaps=_dedupe(environment_gaps),
                )
            if impact.kind == DiscoveredImpactKind.OBSERVATION:
                kind = PipelineLoopTriggerKind.MISSING_WORLD_OBSERVATION if plan and plan.must_change_world else PipelineLoopTriggerKind.MISSING_REPOSITORY_OBSERVATION
                return PipelineLoopTrigger(
                    kind=kind,
                    reason="Additional observation is required before bounded work can continue.",
                    missing_evidence=_dedupe(missing_evidence),
                    missing_obligations=_dedupe(missing_obligations),
                    blocker_ids=blocker_ids,
                    discovered_impacts=[impact.kind.value],
                    blocker_summaries=[impact.summary],
                    environment_gaps=_dedupe(environment_gaps),
                )
            if impact.kind == DiscoveredImpactKind.DOCUMENTATION:
                return self._impact_trigger(PipelineLoopTriggerKind.DOCS_IMPACT_DISCOVERED, impact, missing_evidence, missing_obligations, blocker_ids, discovered_impacts, environment_gaps)
            if impact.kind == DiscoveredImpactKind.EXAMPLES:
                return self._impact_trigger(PipelineLoopTriggerKind.EXAMPLES_IMPACT_DISCOVERED, impact, missing_evidence, missing_obligations, blocker_ids, discovered_impacts, environment_gaps)
            if impact.kind == DiscoveredImpactKind.CI_BUILD:
                return self._impact_trigger(PipelineLoopTriggerKind.CI_BUILD_IMPACT_DISCOVERED, impact, missing_evidence, missing_obligations, blocker_ids, discovered_impacts, environment_gaps)
            if impact.kind == DiscoveredImpactKind.CODEGEN_TOOLING:
                return self._impact_trigger(PipelineLoopTriggerKind.CODEGEN_BUILD_IMPACT_DISCOVERED, impact, missing_evidence, missing_obligations, blocker_ids, discovered_impacts, environment_gaps)
            if impact.kind == DiscoveredImpactKind.INTEGRATION:
                return self._impact_trigger(PipelineLoopTriggerKind.INTEGRATION_SCOPE_DISCOVERED, impact, missing_evidence, missing_obligations, blocker_ids, discovered_impacts, environment_gaps)
            if impact.kind == DiscoveredImpactKind.SETUP:
                return self._impact_trigger(PipelineLoopTriggerKind.SETUP_GAP_DISCOVERED, impact, missing_evidence, missing_obligations, blocker_ids, discovered_impacts, environment_gaps)

        text = _lower_join([
            *(verification.missing_evidence if verification else []),
            *(verification.missing_obligations if verification else []),
            *(verification.checks_failed if verification else []),
            verification.summary if verification else "",
            publish.summary if publish else "",
            publish.evidence_text if publish else "",
            *(obligations.blocker_conditions if obligations else []),
        ])
        if any(marker in text for marker in ("documentation impact", "docs impact", "readme update", "documentation update")):
            return PipelineLoopTrigger(
                kind=PipelineLoopTriggerKind.DOCS_IMPACT_DISCOVERED,
                reason="Documentation impact discovered from runtime evidence.",
                missing_evidence=_dedupe(missing_evidence),
                missing_obligations=_dedupe(missing_obligations),
                blocker_ids=blocker_ids,
                discovered_impacts=_dedupe([*discovered_impacts, "documentation"]),
                blocker_summaries=_dedupe(blocker_summaries),
                environment_gaps=_dedupe(environment_gaps),
            )
        if any(marker in text for marker in ("integration impact", "integration path", "integration update", "integration validation")):
            return PipelineLoopTrigger(
                kind=PipelineLoopTriggerKind.INTEGRATION_SCOPE_DISCOVERED,
                reason="Integration impact discovered from runtime evidence.",
                missing_evidence=_dedupe(missing_evidence),
                missing_obligations=_dedupe(missing_obligations),
                blocker_ids=blocker_ids,
                discovered_impacts=_dedupe([*discovered_impacts, "integration"]),
                blocker_summaries=_dedupe(blocker_summaries),
                environment_gaps=_dedupe(environment_gaps),
            )
        if any(marker in text for marker in ("setup impact", "environment missing", "environment prerequisite", "setup step missing")):
            return PipelineLoopTrigger(
                kind=PipelineLoopTriggerKind.ENVIRONMENT_PREREQUISITE_MISSING,
                reason="Environment/setup prerequisite is missing according to runtime evidence.",
                missing_evidence=_dedupe(missing_evidence),
                missing_obligations=_dedupe(missing_obligations),
                blocker_ids=blocker_ids,
                discovered_impacts=_dedupe([*discovered_impacts, "setup"]),
                blocker_summaries=_dedupe(blocker_summaries),
                environment_gaps=_dedupe(environment_gaps),
            )
        if any(marker in text for marker in ("ci impact", "build impact", "codegen impact", "codegen update", "ci/build")):
            return PipelineLoopTrigger(
                kind=PipelineLoopTriggerKind.CI_BUILD_IMPACT_DISCOVERED,
                reason="CI/build impact discovered from runtime evidence.",
                missing_evidence=_dedupe(missing_evidence),
                missing_obligations=_dedupe(missing_obligations),
                blocker_ids=blocker_ids,
                discovered_impacts=_dedupe([*discovered_impacts, "ci_build"]),
                blocker_summaries=_dedupe(blocker_summaries),
                environment_gaps=_dedupe(environment_gaps),
            )
        if any(marker in text for marker in ("context missing", "context packet insufficient")):
            return PipelineLoopTrigger(kind=PipelineLoopTriggerKind.MISSING_CONTEXT, reason="ContextPacket is insufficient; rebuild context from artifacts.", missing_evidence=_dedupe(missing_evidence), missing_obligations=_dedupe(missing_obligations))
        if any(marker in text for marker in ("plan incomplete", "replan", "replanning needed")):
            return PipelineLoopTrigger(kind=PipelineLoopTriggerKind.PLAN_INCOMPLETE, reason="Current plan is incomplete for the discovered work surface.", missing_evidence=_dedupe(missing_evidence), missing_obligations=_dedupe(missing_obligations))
        if any(marker in text for marker in ("packet progression", "requires replanning", "rediscovery")):
            return PipelineLoopTrigger(kind=PipelineLoopTriggerKind.PACKET_REPLANNING_REQUIRED, reason="Packet progression requires upstream rediscovery or replanning.", missing_evidence=_dedupe(missing_evidence), missing_obligations=_dedupe(missing_obligations))
        if any(marker in text for marker in ("repair exhausted", "repair attempts exhausted")):
            return PipelineLoopTrigger(kind=PipelineLoopTriggerKind.REPAIR_EXHAUSTED_REDISCOVERY_REQUIRED, reason="Repair attempts are exhausted; upstream rediscovery is required.", missing_evidence=_dedupe(missing_evidence), missing_obligations=_dedupe(missing_obligations))
        return PipelineLoopTrigger(kind=PipelineLoopTriggerKind.NONE, reason="No re-entry trigger text was present.")

    def next_stage(self, decision: PipelineLoopDecision) -> str | None:
        if decision.target_stage in {PipelineReentryTarget.CONTINUE}:
            return None
        return decision.target_stage.value

    def _target_for_trigger(self, trigger: PipelineLoopTriggerKind) -> PipelineReentryTarget:
        mapping = {
            PipelineLoopTriggerKind.MISSING_RESEARCH_EVIDENCE: PipelineReentryTarget.RESEARCH,
            PipelineLoopTriggerKind.MISSING_REPOSITORY_OBSERVATION: PipelineReentryTarget.OBSERVE,
            PipelineLoopTriggerKind.MISSING_WORLD_OBSERVATION: PipelineReentryTarget.OBSERVE,
            PipelineLoopTriggerKind.MISSING_CONTEXT: PipelineReentryTarget.BUILD_CONTEXT,
            PipelineLoopTriggerKind.MISSING_OBLIGATIONS: PipelineReentryTarget.OBLIGATIONS,
            PipelineLoopTriggerKind.PLAN_INCOMPLETE: PipelineReentryTarget.PLAN,
            PipelineLoopTriggerKind.VERIFICATION_MISSING_EVIDENCE: PipelineReentryTarget.OBLIGATIONS,
            PipelineLoopTriggerKind.ACCEPTANCE_MISSING_REQUIRED_OBLIGATIONS: PipelineReentryTarget.OBLIGATIONS,
            PipelineLoopTriggerKind.NEW_SIDE_EFFECTS_DISCOVERED: PipelineReentryTarget.OBLIGATIONS,
            PipelineLoopTriggerKind.DOCS_IMPACT_DISCOVERED: PipelineReentryTarget.OBLIGATIONS,
            PipelineLoopTriggerKind.EXAMPLES_IMPACT_DISCOVERED: PipelineReentryTarget.OBLIGATIONS,
            PipelineLoopTriggerKind.CI_BUILD_IMPACT_DISCOVERED: PipelineReentryTarget.OBLIGATIONS,
            PipelineLoopTriggerKind.CODEGEN_BUILD_IMPACT_DISCOVERED: PipelineReentryTarget.OBLIGATIONS,
            PipelineLoopTriggerKind.INTEGRATION_SCOPE_DISCOVERED: PipelineReentryTarget.OBLIGATIONS,
            PipelineLoopTriggerKind.SETUP_GAP_DISCOVERED: PipelineReentryTarget.OBSERVE,
            PipelineLoopTriggerKind.PUBLISH_DEEPER_PLANNING_REQUIRED: PipelineReentryTarget.PLAN,
            PipelineLoopTriggerKind.ENVIRONMENT_PREREQUISITE_MISSING: PipelineReentryTarget.OBSERVE,
            PipelineLoopTriggerKind.PACKET_REPLANNING_REQUIRED: PipelineReentryTarget.PLAN,
            PipelineLoopTriggerKind.REPAIR_EXHAUSTED_REDISCOVERY_REQUIRED: PipelineReentryTarget.OBSERVE,
        }
        return mapping.get(trigger, PipelineReentryTarget.CONTINUE)

    @staticmethod
    def _impact_trigger(
        kind: PipelineLoopTriggerKind,
        impact,
        missing_evidence: Iterable[str],
        missing_obligations: Iterable[str],
        blocker_ids: Iterable[str],
        discovered_impacts: Iterable[str],
        environment_gaps: Iterable[str],
    ) -> PipelineLoopTrigger:
        return PipelineLoopTrigger(
            kind=kind,
            reason=impact.summary,
            missing_evidence=_dedupe(missing_evidence),
            missing_obligations=_dedupe(missing_obligations),
            blocker_ids=_dedupe(blocker_ids),
            discovered_impacts=_dedupe([*discovered_impacts, impact.kind.value]),
            blocker_summaries=[impact.summary],
            environment_gaps=_dedupe(environment_gaps),
        )


def _coerce_loop_decision(item: PipelineLoopDecision | dict[str, object]) -> PipelineLoopDecision:
    if isinstance(item, PipelineLoopDecision):
        return item
    return PipelineLoopDecision.model_validate(item)


def _lower_join(items: Iterable[object]) -> str:
    return " ".join(str(item).lower() for item in items if str(item).strip())


def _dedupe(items: Iterable[object]) -> list[str]:
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return out
