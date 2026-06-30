from __future__ import annotations

from .base import BaseWorkflowStageNodes
from .intake import IntakeStageMixin
from .observation_context import ObservationContextStageMixin
from .planning_policy import PlanningPolicyStageMixin
from .execution import ExecutionStageMixin
from .publishing import PublishingStageMixin
from .verification_acceptance import VerificationAcceptanceStageMixin


class WorkflowStageNodes(
    IntakeStageMixin,
    ObservationContextStageMixin,
    PlanningPolicyStageMixin,
    ExecutionStageMixin,
    PublishingStageMixin,
    VerificationAcceptanceStageMixin,
    BaseWorkflowStageNodes,
):
    """Composed stage-node facade for the workflow graph.

    Each mixin owns one logical stage group; ``graph.workflow`` remains a
    composition root and no longer carries stage implementation details.
    """


__all__ = ["WorkflowStageNodes"]
