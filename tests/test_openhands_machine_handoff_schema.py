from __future__ import annotations

import asyncio

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.evidence import EvidenceExtractor
from artifact_workflow_runtime.models import (
    BackendKind,
    ExecutionFamily,
    ExecutionRequest,
    ObservationRequest,
    VerificationMode,
    VerificationRequest,
)
from artifact_workflow_runtime.openhands_adapter.adapter import OpenHandsAdapter
from artifact_workflow_runtime.openhands_adapter.models import AppConversationStart, OpenHandsRunResult


REAL_OBSERVE_HANDOFF_SHAPE = r'''```json
{
  "summary": "Observed repo facts",
  "structured_evidence": {
    "facts": {
      "repo_root": "/workspace/project",
      "dotnet_sdk_installed": false,
      "nuget_dependencies": ["Google.Protobuf 3.28.2"]
    },
    "commands_run": [
      {"command": "dotnet --version", "exit_code": 127, "summary": ".NET SDK not installed"}
    ],
    "files_observed": {
      "project_file": "/workspace/project/grpc/csharp/Freeplane.Grpc.csproj",
      "generated_stubs": [
        "/workspace/project/grpc/csharp/obj/Debug/net8.0/proto/Freeplane.cs"
      ]
    },
    "blockers": [".NET SDK 8.0+ is not installed"]
  },
  "blockers": ["No C# tests are included in CI"],
  "notes": "Compatibility shape seen from OpenHands follow-up."
}
```'''


class ProseThenMachineJsonInstance:
    def __init__(self) -> None:
        self.initial_prompts: list[str] = []
        self.followup_prompts: list[str] = []
        self.start = AppConversationStart(conversation_id="conv-1")

    async def run(self, *, prompt: str, **kwargs) -> OpenHandsRunResult:
        self.initial_prompts.append(prompt)
        return OpenHandsRunResult(text="Initial prose report", status="finished", conversation_id="conv-1", start=self.start)

    async def followup(self, *, conversation, prompt: str) -> OpenHandsRunResult:
        self.followup_prompts.append(prompt)
        return OpenHandsRunResult(
            text='{"summary":"ok","structured_evidence":{"commands_run":[{"command":"pytest","exit_code":0,"output_excerpt":"passed"}],"blockers":[]},"blockers":[]}',
            status="finished",
            conversation_id=conversation.conversation_id,
            start=conversation,
        )


def test_openhands_initial_packets_do_not_include_machine_json_schema() -> None:
    requests = [
        ObservationRequest(task_id="task", execution_family=ExecutionFamily.REPOSITORY_CHANGE, prompt="observe"),
        ExecutionRequest(task_id="task", execution_family=ExecutionFamily.REPOSITORY_CHANGE, prompt="execute"),
        VerificationRequest(
            execution_result_id="exec",
            execution_family=ExecutionFamily.REPOSITORY_CHANGE,
            backend=BackendKind.OPENHANDS,
            mode=VerificationMode.WORLD_CHECK,
            prompt="verify",
            checks=["pytest"],
        ),
    ]
    for request in requests:
        prompt = request.compiled_prompt()
        assert "BEGIN_JSON_SCHEMA" not in prompt
        assert "END_JSON_SCHEMA" not in prompt
        assert "OpenHandsMachineHandoff" not in prompt
        assert "additionalProperties" not in prompt
        assert "First OpenHands pass must return a concise human-readable operational report only" in prompt
        assert "the controller will request the canonical JSON handoff in a separate follow-up" in prompt
        assert "Return JSON only." not in prompt


def test_openhands_followup_prompt_includes_machine_json_schema(tmp_path) -> None:
    instance = ProseThenMachineJsonInstance()
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
    assert instance.initial_prompts
    assert "BEGIN_JSON_SCHEMA" not in instance.initial_prompts[0]
    assert "Return JSON only." not in instance.initial_prompts[0]
    assert instance.followup_prompts and "BEGIN_JSON_SCHEMA" in instance.followup_prompts[0]
    assert "END_JSON_SCHEMA" in instance.followup_prompts[0]
    assert "additionalProperties" in instance.followup_prompts[0]
    assert "Return JSON only." in instance.followup_prompts[0]


def test_strict_extractor_accepts_real_openhands_fenced_handoff_shape() -> None:
    evidence = EvidenceExtractor().from_agent_output(REAL_OBSERVE_HANDOFF_SHAPE, artifact_id="raw", strict=True)

    assert evidence.commands_run[0].command == "dotnet --version"
    assert evidence.commands_run[0].output_excerpt == ".NET SDK not installed"
    assert any(item.subject == "repo_root" for item in evidence.extracted_facts)
    assert any(item.path.endswith("Freeplane.Grpc.csproj") for item in evidence.files_observed)
    assert len(evidence.blockers) == 2
