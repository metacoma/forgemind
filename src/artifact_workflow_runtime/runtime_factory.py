from __future__ import annotations

from pathlib import Path

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.controller import WorkflowController
from artifact_workflow_runtime.llm_backend import OpenAICompatibleLLMBackend
from artifact_workflow_runtime.model_routing import ModelRoutingConfig, DEFAULT_CANONICAL_MODEL, load_model_routing_config, normalize_canonical_model_name
from artifact_workflow_runtime.openhands_adapter import OpenHandsAdapter, OpenHandsInstance
from artifact_workflow_runtime.policy import StaticApprovalProvider
from artifact_workflow_runtime.runtime_events import EventSink
from artifact_workflow_runtime.strategy import StrategySelectionMode


def build_controller(
    *,
    artifact_dir: str,
    direct_llm_endpoint: str,
    direct_llm_model: str = DEFAULT_CANONICAL_MODEL,
    direct_llm_api_key: str | None,
    openhands_endpoint: str,
    openhands_model: str = DEFAULT_CANONICAL_MODEL,
    openhands_api_key: str | None,
    reuse: bool,
    sandbox_id: str | None,
    conversation_id: str | None,
    auto_approve: bool,
    config_path: str | None = None,
    event_sink: EventSink | None = None,
    strategy_selection_mode: StrategySelectionMode | str = StrategySelectionMode.RULE_BASED,
) -> WorkflowController:
    artifact_store = ArtifactStore(artifact_dir)
    model_routing: ModelRoutingConfig = load_model_routing_config(config_path) or ModelRoutingConfig.defaults()
    llm = OpenAICompatibleLLMBackend(direct_llm_endpoint, normalize_canonical_model_name(direct_llm_model) or DEFAULT_CANONICAL_MODEL, api_key=direct_llm_api_key)
    openhands_instance = OpenHandsInstance(
        openhands_endpoint,
        api_key=openhands_api_key,
        default_model=normalize_canonical_model_name(openhands_model) or DEFAULT_CANONICAL_MODEL,
        reuse_sandbox=reuse,
        sandbox_id=sandbox_id,
        conversation_id=conversation_id,
        event_sink=event_sink,
        model_routing=model_routing,
    )
    openhands_adapter = OpenHandsAdapter(openhands_instance, artifact_store, model_routing=model_routing)
    return WorkflowController(
        llm_backend=llm,
        openhands_adapter=openhands_adapter,
        artifact_root=Path(artifact_dir),
        approval_provider=StaticApprovalProvider(approve=auto_approve, reviewer="cli"),
        event_sink=event_sink,
        model_routing=model_routing,
        strategy_selection_mode=strategy_selection_mode,
    )
