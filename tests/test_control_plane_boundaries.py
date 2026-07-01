from __future__ import annotations

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.context import ContextBuilder
from artifact_workflow_runtime.control_plane import RuntimeKernel
from artifact_workflow_runtime.models import (
    Capability,
    ExecutionFamily,
    ExecutionPlan,
    ExecutionRequest,
    ExecutionResult,
    ObservationRequest,
    RoutingDecision,
    Task,
    TaskClassification,
    WorkPacketKind,
)
from artifact_workflow_runtime.openhands_adapter.adapter import OpenHandsAdapter
from artifact_workflow_runtime.openhands_adapter.models import AppConversationStart, OpenHandsRunResult


class DummyInstance:
    default_model = "dummy"

    async def run(self, *, prompt: str, model: str | None = None, title: str | None = None) -> OpenHandsRunResult:
        start = AppConversationStart(conversation_id="conv", sandbox_id="sandbox")
        return OpenHandsRunResult(text="evidence", status="finished", conversation_id="conv", start=start)


def test_runtime_kernel_owns_route_decisions() -> None:
    kernel = RuntimeKernel()
    decision = RoutingDecision(
        needs_repository_observation=True,
        needs_world_observation=False,
        needs_fresh_external_research=True,
        can_plan_immediately=False,
        reasoning="Need current docs before repo observation.",
    )
    assert kernel.next_after_route(decision) == "observe"
    assert kernel.next_after_research(decision) == "observe"


def test_context_packet_is_built_from_artifacts(tmp_path) -> None:
    task = Task(description="Inspect repo facts")
    store = ArtifactStore(tmp_path)
    evidence = store.add_text("observation_evidence", "repo root: /workspace/repo\npytest command: pytest")
    packet = ContextBuilder().build(task, [evidence], artifact_texts={evidence.id: store.read_text(evidence.id)})
    assert evidence.id in packet.artifact_ids
    assert "repo root" in packet.text
    assert "pytest command" in packet.text


def test_openhands_adapter_rejects_wrong_packet_kind(tmp_path) -> None:
    adapter = OpenHandsAdapter(DummyInstance(), ArtifactStore(tmp_path))
    request = ExecutionRequest(
        task_id="task",
        execution_family=ExecutionFamily.REPOSITORY_CHANGE,
        work_packet_kind=WorkPacketKind.VERIFY,
        capabilities=[Capability.REPO_WRITE],
        prompt="bad packet",
    )
    try:
        import pytest
        with pytest.raises(ValueError, match="execute/publish packets"):
            import asyncio
            asyncio.run(adapter.execute(request))
    except RuntimeError as exc:
        # If already inside an event loop in a custom runner, fail with a useful message.
        raise AssertionError("adapter boundary test must run outside an active event loop") from exc


def test_observation_request_declares_bounds() -> None:
    request = ObservationRequest(
        task_id="task",
        execution_family=ExecutionFamily.REPOSITORY_CHANGE,
        capabilities=[Capability.REPO_READ],
        prompt="observe repo",
    )
    assert request.work_packet_kind == WorkPacketKind.OBSERVE
    assert "edit_files" in request.forbidden_actions
    assert "commands_run" in request.expected_outputs
