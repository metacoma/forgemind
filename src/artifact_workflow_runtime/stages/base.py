from __future__ import annotations

from artifact_workflow_runtime.graph.services import WorkflowServices
from artifact_workflow_runtime.graph.stage_gates import StageReadinessGate


class BaseWorkflowStageNodes:
    def __init__(self, services: WorkflowServices) -> None:
        self.services = services
        self.readiness_gate = StageReadinessGate()
