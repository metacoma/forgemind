from __future__ import annotations

from pathlib import Path

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.context import ContextBuilder
from artifact_workflow_runtime.control_plane import RuntimeKernel
from artifact_workflow_runtime.graph import WorkflowServices, build_workflow_graph
from artifact_workflow_runtime.models import FinalReport, Task
from artifact_workflow_runtime.models.state import WorkflowStateSnapshot, validate_workflow_state
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
            runtime_kernel=RuntimeKernel(),
        )
        if self.services.approval_provider is None:
            from artifact_workflow_runtime.policy import StaticApprovalProvider
            self.services.approval_provider = StaticApprovalProvider(approve=False)
        self.graph = build_workflow_graph(self.services)

    async def run(self, task: Task) -> FinalReport:
        initial_snapshot = WorkflowStateSnapshot(task=task)
        result_state = await self.graph.ainvoke(initial_snapshot.to_graph_state())
        final_snapshot = validate_workflow_state(result_state)
        # Persist the typed state snapshot as a durable runtime artifact. The final
        # report remains the public result, but the state artifact is the process
        # source of truth for debugging, repair loops, and future resumability.
        self.artifact_store.add_json("workflow_state_snapshot", final_snapshot.model_dump(mode="json"), metadata={"task_id": task.id})
        if final_snapshot.final_report is None:
            raise RuntimeError("workflow finished without a final_report")
        return final_snapshot.final_report
