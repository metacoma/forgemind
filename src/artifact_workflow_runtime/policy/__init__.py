from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from artifact_workflow_runtime.capabilities.catalog import MUTATING_CAPABILITIES
from artifact_workflow_runtime.models import ApprovalRequest, Capability, ExecutionPlan, PolicyDecision, TaskClassification


class ApprovalProvider(Protocol):
    async def review(self, request: ApprovalRequest) -> ApprovalRequest: ...


class StaticApprovalProvider:
    def __init__(self, *, approve: bool, reviewer: str | None = None) -> None:
        self.approve = approve
        self.reviewer = reviewer

    async def review(self, request: ApprovalRequest) -> ApprovalRequest:
        return request.model_copy(
            update={
                "approved": self.approve,
                "reviewer": self.reviewer,
                "resolved_at": datetime.now(timezone.utc).isoformat(),
            }
        )


class PolicyEngine:
    """Rule-based control layer between planning and OpenHands execution."""

    def decide(self, classification: TaskClassification, plan: ExecutionPlan) -> PolicyDecision:
        caps = self._merged_capabilities(classification, plan)
        reasons: list[str] = []
        requires_approval = self._requires_approval(caps, plan)
        if requires_approval:
            reasons.append("Mutating capabilities or world-changing plan require explicit approval before OpenHands execution.")
        else:
            reasons.append("Read-only or analysis-only plan is allowed without approval.")
        return PolicyDecision(
            allowed=True,
            blocked=False,
            requires_approval=requires_approval,
            reasons=reasons,
            execution_family=plan.execution_family,
            capabilities=caps,
        )

    @staticmethod
    def _merged_capabilities(classification: TaskClassification, plan: ExecutionPlan) -> list[Capability]:
        result: list[Capability] = []
        for cap in [*classification.capabilities, *plan.capabilities]:
            if cap not in result:
                result.append(cap)
        return result

    @staticmethod
    def _requires_approval(caps: list[Capability], plan: ExecutionPlan) -> bool:
        return bool(plan.requires_mutation or plan.must_change_world or any(cap in MUTATING_CAPABILITIES for cap in caps))

from .acl import (
    RuntimeAction,
    RuntimeSubject,
    RuntimeResource,
    ActionDecision,
    PolicyDecisionPoint,
    StaticStagePolicyDecisionPoint,
    PolicyEnforcementPoint,
    PolicyEnforcementError,
)
