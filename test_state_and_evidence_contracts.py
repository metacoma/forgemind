from __future__ import annotations

import asyncio

import pytest

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.context import ContextBuilder
from artifact_workflow_runtime.evidence import EvidenceExtractor
from artifact_workflow_runtime.models import (
    BackendKind,
    ExecutionFamily,
    ExecutionRequest,
    ObservationRequest,
    Task,
    VerificationMode,
    VerificationRequest,
    WorkPacketKind,
)
from artifact_workflow_runtime.models.state import WorkflowStateSnapshot, WorkflowStatus, validate_workflow_state
from artifact_workflow_runtime.openhands_adapter.adapter import OpenHandsAdapter
from artifact_workflow_runtime.openhands_adapter.models import AppConversationStart, OpenHandsRunResult


class DummyInstance:
    default_model = "dummy"

    async def run(self, *, prompt: str, model: str | None = None, title: str | None = None) -> OpenHandsRunResult:
        start = AppConversationStart(conversation_id="conv", sandbox_id="sandbox")
        return OpenHandsRunResult(
            text=(
                '{"structured_evidence": {'
                '"commands_run": [{"command": "pytest tests/test_contracts.py", "exit_code": 0}], '
                '"files_changed": [{"path": "src/app.py", "action": "changed"}], '
                '"tests": [{"name": "pytest", "passed": true, "status": "passed"}], '
                '"blockers": []}}'
            ),
            status="finished",
            conversation_id="conv",
            start=start,
        )


def test_workflow_state_snapshot_validates_graph_wire_state() -> None:
    task = Task(description="fix repo")
    snapshot = WorkflowStateSnapshot(task=task).with_transition(
        stage="intake",
        to_status=WorkflowStatus.INTAKE_COMPLETED,
        reason="task persisted",
        artifact_ids_added=["artifact_1"],
    )
    wire = snapshot.to_graph_state()
    parsed = validate_workflow_state(wire)
    assert parsed.task.id == task.id
    assert parsed.status == WorkflowStatus.INTAKE_COMPLETED
    assert parsed.transitions[0].stage == "intake"


def test_evidence_extractor_builds_structured_bundle_from_agent_text() -> None:
    evidence = EvidenceExtractor().from_text(
        "$ pytest tests/test_app.py\n"
        "modified src/app.py\n"
        "Fact: repo root is /workspace/repo\n"
        "pytest passed\n"
        "Blocker: missing optional integration service",
        artifact_id="artifact_raw",
        changed_default=True,
    )
    assert evidence.commands_run[0].command == "pytest tests/test_app.py"
    assert any(item.path == "src/app.py" for item in evidence.files_changed)
    assert evidence.tests and evidence.tests[0].status in {"passed", "unknown"}
    assert evidence.blockers[0].severity in {"medium", "high"}


def test_openhands_execute_returns_structured_evidence_bundle(tmp_path) -> None:
    adapter = OpenHandsAdapter(DummyInstance(), ArtifactStore(tmp_path))
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
    assert result.evidence_bundle is not None
    assert result.structured_evidence is not None
    assert result.structured_evidence.commands_run[0].command == "pytest tests/test_contracts.py"
    assert any(artifact.kind == "structured_evidence_bundle" for artifact in result.artifacts)


def test_observe_contract_rejects_mutating_allowed_actions(tmp_path) -> None:
    adapter = OpenHandsAdapter(DummyInstance(), ArtifactStore(tmp_path))
    request = ObservationRequest(
        task_id="task",
        execution_family=ExecutionFamily.REPOSITORY_CHANGE,
        prompt="bad observe",
        allowed_actions=["read_files", "edit_files"],
    )
    with pytest.raises(ValueError, match="cannot allow mutating"):
        asyncio.run(adapter.observe(request))


def test_openhands_verify_requires_world_check_contract(tmp_path) -> None:
    adapter = OpenHandsAdapter(DummyInstance(), ArtifactStore(tmp_path))
    request = VerificationRequest(
        execution_result_id="exec",
        execution_family=ExecutionFamily.REPOSITORY_CHANGE,
        backend=BackendKind.DIRECT_LLM,
        mode=VerificationMode.EVIDENCE_REVIEW,
        prompt="bad verify",
    )
    with pytest.raises(ValueError, match="backend=openhands and mode=world_check"):
        asyncio.run(adapter.verify(request))


def test_context_packet_contains_structured_evidence_artifact(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    task = Task(description="reason over evidence")
    raw = store.add_text("execution_evidence", "$ pytest\npytest passed")
    bundle = EvidenceExtractor().from_text(store.read_text(raw.id), artifact_id=raw.id)
    structured = store.add_json("structured_evidence_bundle", bundle.model_dump(mode="json"))
    packet = ContextBuilder().build(
        task,
        [structured],
        artifact_texts={structured.id: store.read_text(structured.id)},
    )
    assert "commands_run" in packet.text
    assert structured.id in packet.artifact_ids


class ProseWrappedJsonInstance:
    default_model = "dummy"

    async def run(self, *, prompt: str, model: str | None = None, title: str | None = None) -> OpenHandsRunResult:
        start = AppConversationStart(conversation_id="conv", sandbox_id="sandbox")
        return OpenHandsRunResult(
            text="""Here is the structured observation output:

```json
{
  "summary": "Observed repo facts",
  "structured_evidence": {
    "commands_run": [{"command": "git status --short", "exit_code": 0}],
    "files_observed": [{"path": "README.md", "action": "observed"}],
    "blockers": []
  },
  "blockers": []
}
```""",
            status="finished",
            conversation_id="conv",
            start=start,
        )


class TokenizedRemoteInstance:
    default_model = "dummy"

    async def run(self, *, prompt: str, model: str | None = None, title: str | None = None) -> OpenHandsRunResult:
        start = AppConversationStart(conversation_id="conv", sandbox_id="sandbox")
        return OpenHandsRunResult(
            text=(
                '{"summary":"Observed repo facts","structured_evidence":{'
                '"commands_run":[{"command":"git remote -v","output_excerpt":"origin https://ghp_SECRET123@github.com/org/repo.git"}],'
                '"extracted_facts":[{"subject":"remote","fact":"https://ghp_SECRET123@github.com/org/repo.git","confidence":"high"}],'
                '"blockers":[]}}'
            ),
            status="finished",
            conversation_id="conv",
            start=start,
        )


def test_openhands_observe_accepts_fenced_json_wrapped_in_prose(tmp_path) -> None:
    adapter = OpenHandsAdapter(ProseWrappedJsonInstance(), ArtifactStore(tmp_path))
    result = asyncio.run(
        adapter.observe(
            ObservationRequest(
                task_id="task",
                execution_family=ExecutionFamily.REPOSITORY_CHANGE,
                prompt="observe repo",
            )
        )
    )
    assert result.ok is True
    assert result.evidence_kind == "agent_text"
    assert result.structured_evidence.commands_run[0].command == "git status --short"
    assert result.structured_evidence.files_observed[0].path == "README.md"


def test_openhands_raw_artifact_masks_tokenized_remote_urls(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    adapter = OpenHandsAdapter(TokenizedRemoteInstance(), store)
    result = asyncio.run(
        adapter.observe(
            ObservationRequest(
                task_id="task",
                execution_family=ExecutionFamily.REPOSITORY_CHANGE,
                prompt="observe repo",
            )
        )
    )
    assert result.ok is True
    raw_text = store.read_text(result.raw_evidence_artifact_id)
    assert "ghp_SECRET123" not in raw_text
    assert "https://***@github.com/org/repo.git" in raw_text
