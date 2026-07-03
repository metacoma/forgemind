from artifact_workflow_runtime.freshness import FreshnessDecision, RetrievalMode, SourcePreference, RetrievalSourceKind, RetrievalService
from artifact_workflow_runtime.models import Capability, ExecutionFamily, ObservationRequest, Task, TaskClassification, WorkPacketKind
from artifact_workflow_runtime.openhands_adapter.contracts import OpenHandsStageContractGate
from artifact_workflow_runtime.policy import RuntimeAction, allowed_runtime_actions_for_stage


def test_runtime_action_registry_normalizes_freshness_actions() -> None:
    assert RuntimeAction.coerce("inspect_package_registry") == RuntimeAction.INTERNET_SEARCH
    assert RuntimeAction.coerce("inspect_release_notes") == RuntimeAction.INTERNET_SEARCH
    assert RuntimeAction.coerce("read_release_notes") == RuntimeAction.INTERNET_SEARCH
    assert RuntimeAction.coerce("resolve_package_versions") == RuntimeAction.INTERNET_SEARCH


def test_research_stage_acl_allows_normalized_freshness_actions() -> None:
    allowed = allowed_runtime_actions_for_stage("research")
    assert RuntimeAction.INTERNET_SEARCH in allowed


def test_observation_contract_accepts_freshness_retrieval_allowed_actions() -> None:
    task = Task(description="Resolve current package versions from official docs.")
    classification = TaskClassification(
        normalized_task="Resolve current package versions from official docs.",
        needs_world_facts=True,
        execution_family=ExecutionFamily.REPOSITORY_CHANGE,
        task_intent="implement",
        capabilities=[Capability.REPO_READ],
        observation_focus=[],
        reasoning="research",
        risk_level="low",
    )
    decision = FreshnessDecision(
        freshness_required=True,
        retrieval_mode=RetrievalMode.DOCS_PLUS_VERSIONS,
        retrieval_reason="need current package versions",
        docs_resolution_required=True,
        version_resolution_required=True,
        changelog_resolution_required=False,
        triggered_by=["version_resolution"],
        targets=["Grpc.Net.Client"],
        preferred_sources=[SourcePreference(rank=1, source_kind=RetrievalSourceKind.OFFICIAL_DOCUMENTATION, reason="official docs")],
        stage_preference="after_observe",
    )
    request = RetrievalService().build_request(task=task, classification=classification, decision=decision)

    # Must not raise.
    OpenHandsStageContractGate.validate_observation(request)
    assert request.work_packet_kind == WorkPacketKind.RESEARCH
