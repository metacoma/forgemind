from __future__ import annotations

from .base import BaseWorkflowStageNodes
from .intake import IntakeStageMixin
from .observation_context import ObservationContextStageMixin
from .planning_policy import PlanningPolicyStageMixin
from .contract_prep import ContractPrepStageMixin
from .execution import ExecutionStageMixin
from .review_qa import ReviewQAStageMixin
from .publishing import PublishingStageMixin
from .verification_acceptance import VerificationAcceptanceStageMixin


class WorkflowStageNodes(
    IntakeStageMixin,
    ObservationContextStageMixin,
    PlanningPolicyStageMixin,
    ContractPrepStageMixin,
    ExecutionStageMixin,
    ReviewQAStageMixin,
    PublishingStageMixin,
    VerificationAcceptanceStageMixin,
    BaseWorkflowStageNodes,
):
    """Composed stage-node facade for the workflow graph."""


__all__ = ["WorkflowStageNodes"]
