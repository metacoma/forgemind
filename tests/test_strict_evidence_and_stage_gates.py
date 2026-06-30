from __future__ import annotations

import asyncio

import pytest

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.graph.stage_gates import StageReadinessError, StageReadinessGate
from artifact_workflow_runtime.models import ExecutionFamily, ExecutionRequest
from artifact_workflow_runtime.openhands_adapter.adapter import OpenHandsAdapter
from artifact_workflow_runtime.openhands_adapter.models import AppConversationStart, OpenHandsRunResult


class ProseOnlyInstance:
    default_model = "dummy"

    async def run(self, *, prompt: str, model: str | None = None, title: str | None = None) -> OpenHandsRunResult:
        start = AppConversationStart(conversation_id="conv", sandbox_id="sandbox")
        return OpenHandsRunResult(
            text="$ pytest\nmodified src/app.py\npytest passed",
            status="finished",
            conversation_id="conv",
            start=start,
        )


def test_openhands_adapter_default_strict_evidence_rejects_prose_only_output(tmp_path) -> None:
    adapter = OpenHandsAdapter(ProseOnlyInstance(), ArtifactStore(tmp_path))
    result = asyncio.run(
        adapter.execute(
            ExecutionRequest(
                task_id="task",
                execution_family=ExecutionFamily.REPOSITORY_CHANGE,
                prompt="execute bounded packet",
                expected_outputs=["changed_files", "commands_run", "test_results"],
            )
        )
    )

    assert result.ok is False
    assert result.evidence_kind == "evidence_contract_missing"
    assert result.stage_failure is not None
    assert result.stage_failure.failure_kind.value == "evidence_contract_missing"
    assert result.structured_evidence.blockers


def test_openhands_adapter_can_run_legacy_non_strict_evidence_mode(tmp_path) -> None:
    adapter = OpenHandsAdapter(ProseOnlyInstance(), ArtifactStore(tmp_path), strict_evidence=False)
    result = asyncio.run(
        adapter.execute(
            ExecutionRequest(
                task_id="task",
                execution_family=ExecutionFamily.REPOSITORY_CHANGE,
                prompt="execute bounded packet",
                expected_outputs=["changed_files", "commands_run", "test_results"],
            )
        )
    )

    assert result.ok is True
    assert result.structured_evidence.commands_run[0].command == "pytest"


def test_stage_readiness_gate_reports_missing_fields_before_node_logic() -> None:
    gate = StageReadinessGate()
    with pytest.raises(StageReadinessError, match="missing state fields"):
        gate.require({"task": {"id": "task"}}, "plan", "task", "classification", "context_packet", "obligations")
