from __future__ import annotations

from typing import Any

from artifact_workflow_runtime.graph.stage_gates import StageReadinessGate


class BaseWorkflowStageNodes:
    def __init__(self, services: Any) -> None:
        self.services = services
        self.readiness_gate = StageReadinessGate()
