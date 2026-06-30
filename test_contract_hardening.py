from __future__ import annotations

import asyncio
import json

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.context import ContextBuilder
from artifact_workflow_runtime.control_plane import RuntimeKernel
from artifact_workflow_runtime.evidence import EvidenceExtractor
from artifact_workflow_runtime.models import ContextPacket, ExecutionFamily, ExecutionRequest, ObservationRequest, Task
from artifact_workflow_runtime.models.state import WorkflowStateSnapshot, validate_workflow_state
from artifact_workflow_runtime.openhands_adapter.adapter import OpenHandsAdapter
from artifact_workflow_runtime.openhands_adapter.models import AppConversationStart, OpenHandsRunResult


class PromptCapturingInstance:
    default_model = "dummy"

    def __init__(self, text: str) -> None:
        self.text = text
        self.prompts: list[str] = []

    async def run(self, *, prompt: str, model: str | None = None, title: str | None = None) -> OpenHandsRunResult:
        self.prompts.append(prompt)
        start = AppConversationStart(conversation_id="conv", sandbox_id="sandbox")
        return OpenHandsRunResult(text=self.text, status="finished", conversation_id="conv", start=start)


def test_execution_request_prompt_is_compiled_from_typed_contract() -> None:
    request = ExecutionRequest(
        task_id="task",
        execution_family=ExecutionFamily.REPOSITORY_CHANGE,
        prompt="legacy narrative detail",
        plan_steps=["edit src/app.py"],
        expected_outputs=["changed_files", "commands_run", "test_results"],
    )
    compiled = request.compiled_prompt()
    assert "# Bounded OpenHands execution packet" in compiled
    assert "allowed_actions" in compiled
    assert "forbidden_actions" in compiled
    assert "evidence_requirements" not in compiled
    assert "response_format: json" not in compiled
    assert "legacy narrative detail" in compiled


def test_openhands_adapter_sends_compiled_packet_not_raw_prompt(tmp_path) -> None:
    instance = PromptCapturingInstance("$ pytest\nmodified src/app.py\npytest passed")
    adapter = OpenHandsAdapter(instance, ArtifactStore(tmp_path))
    asyncio.run(
        adapter.execute(
            ExecutionRequest(
                task_id="task",
                execution_family=ExecutionFamily.REPOSITORY_CHANGE,
                prompt="raw narrative only",
                expected_outputs=["changed_files", "commands_run", "test_results"],
            )
        )
    )
    assert instance.prompts
    assert "Bounded OpenHands execution packet" in instance.prompts[0]
    assert "raw narrative only" in instance.prompts[0]


def test_structured_json_evidence_is_preferred_over_text_extraction() -> None:
    payload = {
        "structured_evidence": {
            "commands_run": [{"command": "pytest", "exit_code": 0}],
            "files_changed": [{"path": "src/app.py", "action": "changed"}],
            "tests": [{"name": "pytest", "passed": True, "status": "passed"}],
            "blockers": [],
        }
    }
    evidence = EvidenceExtractor().from_agent_output(json.dumps(payload), artifact_id="raw")
    assert evidence.commands_run[0].command == "pytest"
    assert evidence.files_changed[0].path == "src/app.py"
    assert evidence.postcheck_summary.attempted is True


def test_workflow_state_snapshot_keeps_context_packet_typed() -> None:
    task = Task(description="typed context")
    packet = ContextBuilder().build(task, [])
    snapshot = WorkflowStateSnapshot(task=task, context_packet=packet)
    parsed = validate_workflow_state(snapshot.to_graph_state())
    assert isinstance(parsed.context_packet, ContextPacket)
    assert parsed.context_packet.task_id == task.id


def test_runtime_kernel_readiness_reports_missing_fact_fields() -> None:
    task = Task(description="fix repo")
    snapshot = WorkflowStateSnapshot(task=task)
    readiness = RuntimeKernel().planning_readiness(snapshot)
    assert readiness.ready is False
    assert "classification" in readiness.missing_state_fields
    assert "context_packet" in readiness.missing_state_fields


def test_structured_json_evidence_accepts_openhands_handoff_fact_map() -> None:
    payload = {
        "summary": "observation summary",
        "structured_evidence": {
            "facts": {
                "repo_structure": {
                    "root": "/workspace/project",
                    "proto_rpc_count": 27,
                },
                "build_config": "net8.0 project",
            },
            "commands_run": [
                {"command": "pwd && ls -la", "exit_code": 0, "summary": "confirmed workspace"}
            ],
            "files_observed": ["/workspace/project/grpc/csharp/Freeplane.Grpc.csproj"],
        },
        "blockers": ["dotnet command not found"],
        "unknowns": ["whether build succeeds"],
    }

    text = "```json\n" + json.dumps(payload) + "\n```"
    evidence = EvidenceExtractor().from_agent_output(text, artifact_id="raw", strict=True)

    assert evidence.commands_run[0].command == "pwd && ls -la"
    assert evidence.commands_run[0].output_excerpt == "confirmed workspace"
    assert evidence.files_observed[0].path == "/workspace/project/grpc/csharp/Freeplane.Grpc.csproj"
    assert {fact.subject for fact in evidence.extracted_facts} == {"repo_structure", "build_config"}
    assert evidence.blockers[0].summary == "dotnet command not found"


def test_openhands_json_handoff_prompt_includes_runtime_json_schema() -> None:
    from artifact_workflow_runtime.openhands_adapter.adapter import (
        _contract_repair_prompt,
        _json_schema_for_response_contract,
    )

    request = ObservationRequest(
        task_id="task",
        execution_family=ExecutionFamily.REPOSITORY_CHANGE,
        prompt="observe repository",
        focus=["repo structure"],
        required_facts=["repo_structure"],
    )

    schema = json.loads(_json_schema_for_response_contract(request.response_contract))
    prompt = _contract_repair_prompt(
        stage="observation",
        response_contract=request.response_contract,
        evidence_requirements=request.evidence_requirements,
    )

    assert "Your output MUST validate against the JSON Schema below." in prompt
    assert "Do not infer new facts" in prompt
    assert "JSON Schema:" in prompt
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["summary", "structured_evidence", "blockers"]
    assert "structured_evidence" in schema["properties"]
    assert "commands_run" in schema["properties"]["structured_evidence"]["properties"]
    assert "BlockerEvidence" in schema["$defs"]
