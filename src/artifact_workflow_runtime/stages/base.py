from __future__ import annotations

from typing import Any

from artifact_workflow_runtime.graph.stage_gates import StageReadinessGate


class BaseWorkflowStageNodes:
    def __init__(self, services: Any) -> None:
        self.services = services
        self.readiness_gate = StageReadinessGate()


    async def dispatch_node(self, state: dict[str, Any]) -> dict[str, Any]:
        return {}

    def dispatch_next(self, state: dict[str, Any]) -> str:
        target = str(state.get("resume_next_stage") or "intake").strip()
        allowed = {
            "intake",
            "classify",
            "route",
            "research",
            "observe",
            "build_context",
            "obligations",
            "done_contract",
            "plan",
            "policy",
            "approval",
            "workspace_prepare",
            "execute",
            "review",
            "qa_plan",
            "qa_execute",
            "qa_review",
            "repair",
            "acceptance",
            "publish",
            "post_publish_verify",
            "finalize",
        }
        return target if target in allowed else "intake"
