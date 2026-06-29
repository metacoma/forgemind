from __future__ import annotations

from pathlib import Path

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.controller import WorkflowController
from artifact_workflow_runtime.llm_backend import OpenAICompatibleLLMBackend
from artifact_workflow_runtime.openhands_adapter import OpenHandsAdapter, OpenHandsInstance
from artifact_workflow_runtime.policy import StaticApprovalProvider
from artifact_workflow_runtime.runtime_events import EventSink


def build_controller(
    *,
    artifact_dir: str,
    direct_llm_endpoint: str,
    direct_llm_model: str,
    direct_llm_api_key: str | None,
    openhands_endpoint: str,
    openhands_model: str,
    openhands_api_key: str | None,
    reuse: bool,
    sandbox_id: str | None,
    conversation_id: str | None,
    auto_approve: bool,
    event_sink: EventSink | None = None,
) -> WorkflowController:
    artifact_store = ArtifactStore(artifact_dir)
    llm = OpenAICompatibleLLMBackend(direct_llm_endpoint, direct_llm_model, api_key=direct_llm_api_key)
    openhands_instance = OpenHandsInstance(
        openhands_endpoint,
        api_key=openhands_api_key,
        default_model=openhands_model,
        reuse_sandbox=reuse,
        sandbox_id=sandbox_id,
        conversation_id=conversation_id,
    )
    openhands_adapter = OpenHandsAdapter(openhands_instance, artifact_store)
    return WorkflowController(
        llm_backend=llm,
        openhands_adapter=openhands_adapter,
        artifact_root=Path(artifact_dir),
        approval_provider=StaticApprovalProvider(approve=auto_approve, reviewer="cli"),
        event_sink=event_sink,
    )
