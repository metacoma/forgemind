from __future__ import annotations

from pathlib import Path

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.context import ContextBuilder
from artifact_workflow_runtime.graph import WorkflowServices, build_workflow_graph
from artifact_workflow_runtime.models import FinalReport, Task
from artifact_workflow_runtime.observation import ObservationService
from artifact_workflow_runtime.policy import ApprovalProvider, PolicyEngine
from artifact_workflow_runtime.reports import FinalReportBuilder
from artifact_workflow_runtime.runtime_events import EventSink
from artifact_workflow_runtime.model_routing import ModelRoutingConfig


class WorkflowController:
    def __init__(self, *, llm_backend, openhands_adapter, artifact_root: str | Path, approval_provider: ApprovalProvider | None = None, event_sink: EventSink | None = None, model_routing: ModelRoutingConfig | None = None) -> None:
        adapter_store = getattr(openhands_adapter, "artifact_store", None)
        self.artifact_store = adapter_store if isinstance(adapter_store, ArtifactStore) else ArtifactStore(artifact_root)
        self.services = WorkflowServices(
            llm_backend=llm_backend,
            openhands_adapter=openhands_adapter,
            artifact_store=self.artifact_store,
            context_builder=ContextBuilder(),
            observation_service=ObservationService(),
            policy_engine=PolicyEngine(),
            approval_provider=approval_provider or getattr(openhands_adapter, "approval_provider", None),
            final_report_builder=FinalReportBuilder(),
            event_sink=event_sink,
            model_routing=model_routing,
        )
        if self.services.approval_provider is None:
            from artifact_workflow_runtime.policy import StaticApprovalProvider
            self.services.approval_provider = StaticApprovalProvider(approve=False)
        self.graph = build_workflow_graph(self.services)

    async def run(self, task: Task) -> FinalReport:
        initial_state = {
            "task": task.model_dump(mode="json"),
            "artifact_ids": [],
            "errors": [],
            "status": "created",
        }
        result_state = await self.graph.ainvoke(initial_state)
        return FinalReport.model_validate(result_state["final_report"])
