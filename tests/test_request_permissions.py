from __future__ import annotations

import asyncio

import pytest

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.freshness import FreshnessDecision, RetrievalMode, SourcePreference, RetrievalSourceKind
from artifact_workflow_runtime.models import ExecutionFamily, ObservationRequest, Task, TaskClassification, WorkPacketKind
from artifact_workflow_runtime.openhands_adapter.adapter import OpenHandsAdapter
from artifact_workflow_runtime.openhands_adapter.contracts import OpenHandsStageContractGate
from artifact_workflow_runtime.openhands_adapter.models import AppConversationStart, OpenHandsRunResult
from artifact_workflow_runtime.policy import RequestPermissionCatalog, RuntimeAction


class DummyInstance:
    default_model = "dummy"

    async def run(self, *, prompt: str, model: str | None = None, title: str | None = None) -> OpenHandsRunResult:
        start = AppConversationStart(conversation_id="conv", sandbox_id="sandbox")
        return OpenHandsRunResult(text='{"summary":"ok","structured_evidence":{"blockers":[]},"blockers":[]}', status="finished", conversation_id="conv", start=start)


def test_request_permission_catalog_maps_retrieval_permissions_to_runtime_actions() -> None:
    release_notes = RequestPermissionCatalog.require_stage_permission("inspect_release_notes", stage="research")
    package_registry = RequestPermissionCatalog.require_stage_permission("inspect_package_registry", stage="research")

    assert release_notes.runtime_action == RuntimeAction.INTERNET_SEARCH
    assert package_registry.runtime_action == RuntimeAction.INTERNET_SEARCH


def test_request_permission_catalog_rejects_cross_stage_permission_leakage() -> None:
    with pytest.raises(ValueError, match="not allowed for stage observe"):
        RequestPermissionCatalog.require_stage_permission("inspect_release_notes", stage="observe")


def test_research_packet_accepts_release_notes_and_package_registry_permissions(tmp_path) -> None:
    adapter = OpenHandsAdapter(DummyInstance(), ArtifactStore(tmp_path))
    request = ObservationRequest(
        task_id="task",
        execution_family=ExecutionFamily.REPOSITORY_CHANGE,
        work_packet_kind=WorkPacketKind.RESEARCH,
        prompt="research",
        allowed_actions=[
            "internet_research",
            "read_official_docs",
            "inspect_release_notes",
            "inspect_package_registry",
            "inspect_public_metadata",
            "collect_source_attribution",
        ],
        forbidden_actions=["edit_files", "write_files", "commit", "push", "git push", "create_pr", "open_pull_request", "publish"],
    )

    result = asyncio.run(adapter.observe(request))

    assert result.ok is True


def test_validate_research_packet_contract_directly() -> None:
    request = ObservationRequest(
        task_id="task",
        execution_family=ExecutionFamily.REPOSITORY_CHANGE,
        work_packet_kind=WorkPacketKind.RESEARCH,
        prompt="research",
        allowed_actions=[
            "internet_research",
            "read_official_docs",
            "inspect_release_notes",
            "inspect_package_registry",
            "inspect_public_metadata",
            "collect_source_attribution",
        ],
        forbidden_actions=["edit_files", "write_files", "commit", "push", "git push", "create_pr", "open_pull_request", "publish"],
    )

    OpenHandsStageContractGate.validate_observation(request)
