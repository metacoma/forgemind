from __future__ import annotations

from pathlib import Path

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.context import ContextBuilder
from artifact_workflow_runtime.control_plane import RuntimeKernel
from artifact_workflow_runtime.done_contract import DoneContractCompiler
from artifact_workflow_runtime.environment import EnvironmentDiscovery
from artifact_workflow_runtime.graph import WorkflowServices, build_workflow_graph
from artifact_workflow_runtime.models import FinalReport, Task
from artifact_workflow_runtime.contracts import ContractViolationError
from artifact_workflow_runtime.models.state import WorkflowStateSnapshot, validate_workflow_state
from artifact_workflow_runtime.observation import ObservationService
from artifact_workflow_runtime.policy import ApprovalProvider, PolicyEngine
from artifact_workflow_runtime.publisher import DeterministicPublisher
from artifact_workflow_runtime.qa import DeterministicQARunner, QAPlanner
from artifact_workflow_runtime.reports import FinalReportBuilder
from artifact_workflow_runtime.runtime_events import EventSink
from artifact_workflow_runtime.model_routing import ModelRoutingConfig
from artifact_workflow_runtime.state import WorkflowCheckpointRecorder
from artifact_workflow_runtime.control_plane.recovery import CheckpointStore, RecoveredRuntimeState
from artifact_workflow_runtime.strategy import StrategyArbitrator, StrategyGovernor, StrategySelectionMode
from artifact_workflow_runtime.decomposition import DecompositionPlanner, PacketSelector, DecompositionValidator


class WorkflowController:
    def __init__(self, *, llm_backend, openhands_adapter, artifact_root: str | Path, approval_provider: ApprovalProvider | None = None, event_sink: EventSink | None = None, model_routing: ModelRoutingConfig | None = None, strategy_selection_mode: StrategySelectionMode | str = StrategySelectionMode.RULE_BASED) -> None:
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
            done_contract_compiler=DoneContractCompiler(),
            environment_discovery=EnvironmentDiscovery(),
            qa_planner=QAPlanner(),
            qa_runner=DeterministicQARunner(),
            publisher_backend=DeterministicPublisher(self.artifact_store, scripted_source=openhands_adapter),
            event_sink=event_sink,
            model_routing=model_routing,
            runtime_kernel=RuntimeKernel(),
            checkpoint_recorder=WorkflowCheckpointRecorder(self.artifact_store),
            strategy_governor=StrategyGovernor(),
            strategy_selection_mode=StrategySelectionMode.coerce(strategy_selection_mode),
            strategy_arbitrator=StrategyArbitrator(),
            decomposition_planner=DecompositionPlanner(),
            packet_selector=PacketSelector(),
            decomposition_validator=DecompositionValidator(),
        )
        if self.services.approval_provider is None:
            from artifact_workflow_runtime.policy import StaticApprovalProvider
            self.services.approval_provider = StaticApprovalProvider(approve=False)
        self.graph = build_workflow_graph(self.services)
        self.checkpoint_store = CheckpointStore(self.artifact_store)

    async def _run_snapshot(self, initial_snapshot: WorkflowStateSnapshot) -> FinalReport:
        try:
            result_state = await self.graph.ainvoke(initial_snapshot.to_graph_state())
        except ContractViolationError as exc:
            artifact = self.artifact_store.add_json(
                "contract_violation",
                exc.result.model_dump(mode="json"),
                metadata={"task_id": initial_snapshot.task.id, "source": "direct_llm_contract_gateway"},
            )
            report = FinalReport(
                task_id=initial_snapshot.task.id,
                status="contract_violation",
                summary=str(exc),
                artifact_ids=[artifact.id],
            )
            self.artifact_store.add_json("final_report", report.model_dump(mode="json"), metadata={"task_id": initial_snapshot.task.id, "status": report.status})
            self.artifact_store.add_json("workflow_state_snapshot", initial_snapshot.model_dump(mode="json"), metadata={"task_id": initial_snapshot.task.id, "status": "contract_violation"})
            return report
        final_snapshot = validate_workflow_state(result_state, final=True)
        self.artifact_store.add_json("workflow_state_snapshot", final_snapshot.model_dump(mode="json"), metadata={"task_id": initial_snapshot.task.id})
        if final_snapshot.final_report is None:
            raise RuntimeError("workflow finished without a final_report")
        return final_snapshot.final_report

    async def run(self, task: Task) -> FinalReport:
        return await self._run_snapshot(WorkflowStateSnapshot(task=task))

    def recover(self, task_id: str) -> RecoveredRuntimeState:
        return self.checkpoint_store.recover(task_id=task_id)

    async def resume(self, task_id: str) -> FinalReport:
        recovered = self.recover(task_id)
        if not recovered.resume.allowed:
            raise RuntimeError(recovered.resume.reason)
        snapshot = recovered.snapshot.model_copy(update={
            "resume_next_stage": recovered.resume.resume_from_stage,
            "resume_checkpoint_id": recovered.resume.checkpoint_id,
            "recovered_from_checkpoint": True,
        })
        return await self._run_snapshot(snapshot)
