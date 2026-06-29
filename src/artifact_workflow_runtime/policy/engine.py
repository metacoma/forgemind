from __future__ import annotations

from artifact_workflow_runtime.capabilities import MUTATING_CAPABILITIES
from artifact_workflow_runtime.models import ExecutionPlan, PolicyDecision, TaskClassification


class PolicyEngine:
    def decide(self, classification: TaskClassification, plan: ExecutionPlan) -> PolicyDecision:
        combined_caps = list(dict.fromkeys([*classification.capabilities, *plan.capabilities]))
        requires_approval = any(cap in MUTATING_CAPABILITIES for cap in combined_caps)
        reasons = []
        if requires_approval:
            reasons.append("Plan contains mutating capabilities and requires approval before execution.")
        else:
            reasons.append("Plan is read-only or low-risk and may proceed without approval.")
        return PolicyDecision(
            allowed=True,
            blocked=False,
            requires_approval=requires_approval,
            reasons=reasons,
            execution_family=plan.execution_family,
            capabilities=combined_caps,
        )
