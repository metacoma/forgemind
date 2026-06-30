from __future__ import annotations

import asyncio

import pytest

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.graph.stage_gates import StageReadinessError, StageReadinessGate
from artifact_workflow_runtime.models import ExecutionFamily, ExecutionRequest, ObservationRequest
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




class FollowupRaisesInstance(ProseOnlyInstance):
    async def followup(self, *, conversation, prompt: str):
        raise RuntimeError("followup boom")


class ProseThenJsonInstance(ProseOnlyInstance):
    def __init__(self) -> None:
        self.followup_prompts: list[str] = []

    async def followup(self, *, conversation, prompt: str) -> OpenHandsRunResult:
        self.followup_prompts.append(prompt)
        return OpenHandsRunResult(
            text='{"summary":"execute summary","structured_evidence":{"commands_run":[{"command":"pytest","cwd":null,"exit_code":0,"output_excerpt":"passed"}],"files_changed":["src/app.py"],"tests":[{"name":"pytest","status":"passed","output_excerpt":"1 passed"}],"mutation_summary":{"changed":true,"files_changed":["src/app.py"],"summary":"modified src/app.py"},"postcheck_summary":{"attempted":true,"checks":[],"summary":"pytest passed"},"blockers":[]}}',
            status="finished",
            conversation_id=conversation.conversation_id,
            start=conversation,
        )



class JsonThenJsonInstance(ProseOnlyInstance):
    def __init__(self) -> None:
        self.followup_prompts: list[str] = []

    async def run(self, *, prompt: str, model: str | None = None, title: str | None = None) -> OpenHandsRunResult:
        start = AppConversationStart(conversation_id="conv", sandbox_id="sandbox")
        return OpenHandsRunResult(
            text='{"summary":"initial summary","structured_evidence":{"commands_run":[{"command":"pytest","cwd":null,"exit_code":0,"output_excerpt":"passed"}],"files_changed":["src/app.py"],"tests":[{"name":"pytest","status":"passed","output_excerpt":"1 passed"}],"mutation_summary":{"changed":true,"files_changed":["src/app.py"],"summary":"modified src/app.py"},"postcheck_summary":{"attempted":true,"checks":[],"summary":"pytest passed"},"blockers":[]}}',
            status="finished",
            conversation_id="conv",
            start=start,
        )

    async def followup(self, *, conversation, prompt: str) -> OpenHandsRunResult:
        self.followup_prompts.append(prompt)
        return OpenHandsRunResult(
            text='{"summary":"execute summary","structured_evidence":{"commands_run":[{"command":"pytest","cwd":null,"exit_code":0,"output_excerpt":"passed"}],"files_changed":["src/app.py"],"tests":[{"name":"pytest","status":"passed","output_excerpt":"1 passed"}],"mutation_summary":{"changed":true,"files_changed":["src/app.py"],"summary":"modified src/app.py"},"postcheck_summary":{"attempted":true,"checks":[],"summary":"pytest passed"},"blockers":[]}}',
            status="finished",
            conversation_id=conversation.conversation_id,
            start=conversation,
        )

class ProseThenFencedJsonInstance(ProseOnlyInstance):
    def __init__(self) -> None:
        self.followup_prompts: list[str] = []

    async def followup(self, *, conversation, prompt: str) -> OpenHandsRunResult:
        self.followup_prompts.append(prompt)
        return OpenHandsRunResult(
            text="""``` json
{
  "summary": "execute summary",
  "structured_evidence": {
    "commands_run": [{"command": "pytest", "cwd": null, "exit_code": 0, "output_excerpt": "passed"}],
    "files_changed": ["src/app.py"],
    "tests": [{"name": "pytest", "status": "passed", "output_excerpt": "1 passed"}],
    "mutation_summary": {"changed": true, "files_changed": ["src/app.py"], "summary": "modified src/app.py"},
    "postcheck_summary": {"attempted": true, "checks": [], "summary": "pytest passed"},
    "blockers": []
  }
}
```""",
            status="finished",
            conversation_id=conversation.conversation_id,
            start=conversation,
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


def test_openhands_adapter_retries_in_same_conversation_for_json_handoff(tmp_path) -> None:
    instance = ProseThenJsonInstance()
    adapter = OpenHandsAdapter(instance, ArtifactStore(tmp_path))
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
    assert result.evidence_kind == "agent_text"
    assert result.stage_failure is None
    assert result.structured_evidence.commands_run[0].command == "pytest"
    assert instance.followup_prompts
    assert "Return JSON only." in instance.followup_prompts[0]


def test_openhands_adapter_accepts_fenced_json_on_contract_repair_followup(tmp_path) -> None:
    instance = ProseThenFencedJsonInstance()
    adapter = OpenHandsAdapter(instance, ArtifactStore(tmp_path))
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
    assert result.evidence_kind == "agent_text"
    assert result.stage_failure is None
    assert result.structured_evidence.commands_run[0].command == "pytest"
    assert instance.followup_prompts


def test_openhands_adapter_always_requests_json_handoff_when_followup_is_available(tmp_path) -> None:
    instance = JsonThenJsonInstance()
    adapter = OpenHandsAdapter(instance, ArtifactStore(tmp_path))
    request = ExecutionRequest(
        task_id="task",
        execution_family=ExecutionFamily.REPOSITORY_CHANGE,
        prompt="execute bounded packet",
        expected_outputs=["changed_files", "commands_run", "test_results"],
    )

    result = asyncio.run(adapter.execute(request))

    assert result.ok is True
    assert instance.followup_prompts
    assert "Return JSON only." in instance.followup_prompts[0]
    assert "Return exactly one JSON object" not in request.compiled_prompt()
    assert "response_format: json" not in request.compiled_prompt()


def test_openhands_adapter_reports_json_handoff_followup_failure_without_crashing(tmp_path) -> None:
    adapter = OpenHandsAdapter(FollowupRaisesInstance(), ArtifactStore(tmp_path))
    result = asyncio.run(
        adapter.observe(
            ObservationRequest(
                task_id="task",
                execution_family=ExecutionFamily.REPOSITORY_CHANGE,
                prompt="observe bounded packet",
            )
        )
    )

    assert result.ok is False
    assert result.stage_failure is not None
    assert result.stage_failure.failure_kind.value == "api_error"
    assert "JSON handoff follow-up failed" in result.stage_failure.summary
    assert any(a.kind == "openhands_followup_error" for a in result.artifacts)
