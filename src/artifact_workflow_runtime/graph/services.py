from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.context import ContextBuilder
from artifact_workflow_runtime.control_plane import RuntimeKernel
from artifact_workflow_runtime.model_routing import ModelRoutingConfig
from artifact_workflow_runtime.observation import ObservationService
from artifact_workflow_runtime.openhands_adapter import OpenHandsAdapter
from artifact_workflow_runtime.policy import ApprovalProvider, PolicyEngine
from artifact_workflow_runtime.reports import FinalReportBuilder
from artifact_workflow_runtime.runtime_events import EventSink
from artifact_workflow_runtime.state.checkpoints import WorkflowCheckpointRecorder


@dataclass
class WorkflowServices:
    llm_backend: Any
    openhands_adapter: OpenHandsAdapter | Any
    artifact_store: ArtifactStore
    context_builder: ContextBuilder
    observation_service: ObservationService
    policy_engine: PolicyEngine
    approval_provider: ApprovalProvider
    final_report_builder: FinalReportBuilder
    event_sink: EventSink | None = None
    model_routing: ModelRoutingConfig | None = None
    runtime_kernel: RuntimeKernel | None = None
    checkpoint_recorder: WorkflowCheckpointRecorder | None = None
