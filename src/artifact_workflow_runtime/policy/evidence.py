from __future__ import annotations

from artifact_workflow_runtime.families import family_requires_evidence_gate
from artifact_workflow_runtime.models import ExecutionPlan, ObservationResult, RoutingDecision


class EvidenceGate:
    """Policy-side checks that ensure reasoning/execution is backed by artifacts."""

    def evaluate(
        self,
        *,
        route: RoutingDecision,
        plan: ExecutionPlan,
        research: ObservationResult | None,
        observation: ObservationResult | None,
    ) -> list[str]:
        reasons: list[str] = []
        if family_requires_evidence_gate(plan.execution_family):
            if observation is None:
                reasons.append("Execution requires observation evidence, but no observation result was captured.")
            elif not observation.ok:
                reasons.append("Execution requires usable observation evidence, but observation failed or returned transport garbage.")
        if route.needs_fresh_external_research:
            if research is None:
                reasons.append("Planning and execution require fresh external research evidence, but none was captured.")
            elif not research.ok:
                reasons.append("Fresh external research was required, but the research observation failed or returned unusable evidence.")
        return reasons
