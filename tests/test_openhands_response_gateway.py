from __future__ import annotations

import asyncio
import json

import pytest

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.evidence import EvidenceExtractor
from artifact_workflow_runtime.models import (
    BackendKind,
    ExecutionFamily,
    ExecutionRequest,
    ObservationRequest,
    PublishRequest,
    RepairRequest,
    StructuredResponseContract,
    VerificationMode,
    VerificationRequest,
)
from artifact_workflow_runtime.openhands_adapter import OpenHandsAdapter
from artifact_workflow_runtime.openhands_adapter.gateway import OpenHandsResponseNormalizer
from artifact_workflow_runtime.openhands_adapter.models import AppConversationStart, OpenHandsRunResult


def _payload() -> dict:
    return {
        "summary": "normalized summary",
        "structured_evidence": {
            "commands_run": [{"command": "pytest", "exit_code": 0, "output_excerpt": "passed"}],
            "files_changed": ["src/app.py"],
            "files_observed": ["README.md"],
            "extracted_facts": [{"subject": "repo", "fact": "python project", "confidence": "high"}],
            "tests": [{"name": "pytest", "status": "passed", "output_excerpt": "1 passed"}],
            "blockers": [],
            "mutation_summary": {"changed": True, "files_changed": ["src/app.py"], "summary": "modified src/app.py"},
            "postcheck_summary": {"attempted": True, "checks": [], "summary": "pytest passed"},
        },
        "blockers": [],
    }


def _contract() -> StructuredResponseContract:
    return StructuredResponseContract.for_fields("summary", "structured_evidence", "blockers")


def _normalize(tmp_path, raw_text: str, *, strict: bool = True):
    store = ArtifactStore(tmp_path)
    raw_artifact = store.add_text("openhands_raw_response", raw_text)
    normalizer = OpenHandsResponseNormalizer(store, EvidenceExtractor())
    normalized = normalizer.normalize(
        stage="observation",
        request_id="req-1",
        raw_text=raw_text,
        raw_artifact_id=raw_artifact.id,
        response_contract=_contract(),
        strict=strict,
    )
    return store, raw_artifact, normalized


def test_gateway_accepts_pure_json(tmp_path) -> None:
    raw = json.dumps(_payload())
    store, raw_artifact, normalized = _normalize(tmp_path, raw)

    assert normalized.contract_status == "validated"
    assert normalized.extraction_mode == "raw_json"
    assert normalized.raw_artifact_id == raw_artifact.id
    assert normalized.extracted_payload_artifact_id is not None
    assert normalized.normalized_payload_artifact_id is not None
    assert normalized.structured_evidence is not None
    assert normalized.structured_evidence.commands_run[0].command == "pytest"
    assert store.read_json(normalized.extracted_payload_artifact_id)["structured_evidence"]["commands_run"][0]["command"] == "pytest"


def test_gateway_accepts_fenced_json(tmp_path) -> None:
    raw = "```json\n" + json.dumps(_payload(), indent=2) + "\n```"
    _, _, normalized = _normalize(tmp_path, raw)

    assert normalized.contract_status == "validated"
    assert normalized.extraction_mode == "fenced_json"
    assert normalized.fallback_extraction_used is True


def test_gateway_accepts_preamble_plus_fenced_json(tmp_path) -> None:
    raw = "Here is the machine-readable handoff.\n```json\n" + json.dumps(_payload(), indent=2) + "\n```"
    _, _, normalized = _normalize(tmp_path, raw)

    assert normalized.contract_status == "validated"
    assert normalized.extraction_mode == "fenced_json"
    assert normalized.structured_evidence is not None


def test_gateway_accepts_preamble_plus_raw_json(tmp_path) -> None:
    raw = "Observed useful evidence below.\n" + json.dumps(_payload())
    _, _, normalized = _normalize(tmp_path, raw)

    assert normalized.contract_status == "validated"
    assert normalized.extraction_mode == "scanned_json"


def test_gateway_rejects_prose_only_in_strict_mode(tmp_path) -> None:
    raw = "pytest passed and src/app.py changed"
    _, _, normalized = _normalize(tmp_path, raw)

    assert normalized.contract_status == "missing_structured_json"
    assert normalized.structured_evidence is None


def test_gateway_preserves_provenance_and_structured_fields(tmp_path) -> None:
    raw = "prefix\n```json\n" + json.dumps(_payload(), indent=2) + "\n```\npostfix"
    store, raw_artifact, normalized = _normalize(tmp_path, raw)

    assert raw_artifact.id == normalized.raw_artifact_id
    assert normalized.extracted_payload_artifact_id is not None
    assert normalized.normalized_payload_artifact_id is not None
    normalized_payload = store.read_json(normalized.normalized_payload_artifact_id)
    assert normalized_payload["structured_evidence"]["commands_run"][0]["command"] == "pytest"
    assert normalized_payload["structured_evidence"]["extracted_facts"][0]["fact"] == "python project"


class MixedJsonInstance:
    default_model = "dummy"

    def __init__(self, text: str) -> None:
        self.text = text

    async def run(self, *, prompt: str, model: str | None = None, title: str | None = None) -> OpenHandsRunResult:
        start = AppConversationStart(conversation_id="conv", sandbox_id="sandbox")
        return OpenHandsRunResult(text=self.text, status="finished", conversation_id="conv", start=start)


@pytest.mark.parametrize(
    ("kind", "request_factory"),
    [
        ("observe", lambda: ObservationRequest(task_id="task", execution_family=ExecutionFamily.REPOSITORY_CHANGE, prompt="observe")),
        ("execute", lambda: ExecutionRequest(task_id="task", execution_family=ExecutionFamily.REPOSITORY_CHANGE, prompt="execute", expected_outputs=["changed_files"])),
        (
            "verify",
            lambda: VerificationRequest(
                execution_result_id="exec",
                execution_family=ExecutionFamily.REPOSITORY_CHANGE,
                backend=BackendKind.OPENHANDS,
                mode=VerificationMode.WORLD_CHECK,
                prompt="verify",
                checks=["pytest"],
                allowed_inputs=["filesystem", "shell", "git", "test_runtime", "context_packet_text"],
                forbidden_inputs=[
                    "change_workflow_decision",
                    "declare_task_completed_or_accepted",
                    "expand_task_scope",
                    "edit_files",
                    "write_files",
                    "fix_code",
                    "repair",
                    "commit",
                    "push",
                    "git push",
                    "create_pr",
                    "open_pull_request",
                    "publish",
                ],
            ),
        ),
        ("publish", lambda: PublishRequest(execution_result_id="exec", task_id="task", prompt="publish")),
        (
            "repair",
            lambda: RepairRequest(
                task_id="task",
                execution_result_id="exec",
                execution_family=ExecutionFamily.REPOSITORY_CHANGE,
                prompt="repair",
            ),
        ),
    ],
)
def test_adapter_handles_container_tolerant_json_for_all_openhands_stages(tmp_path, kind: str, request_factory) -> None:
    text = "Short preamble\n```json\n" + json.dumps(_payload(), indent=2) + "\n```"
    adapter = OpenHandsAdapter(MixedJsonInstance(text), ArtifactStore(tmp_path))
    request = request_factory()
    method = getattr(adapter, kind if kind != "verify" else "verify")

    result = asyncio.run(method(request))

    if kind == "verify":
        assert result.passed is True
        evidence_kind = result.evidence_kind
        structured = result.structured_evidence
        artifacts = result.artifacts
    elif kind == "repair":
        assert result.ok is True
        evidence_kind = result.execution_result.evidence_kind
        structured = result.execution_result.structured_evidence
        artifacts = result.execution_result.artifacts
    else:
        assert result.ok is True
        evidence_kind = result.evidence_kind
        structured = result.structured_evidence
        artifacts = result.artifacts
    assert evidence_kind == "agent_text"
    assert structured.commands_run[0].command == "pytest"
    bundle_artifact = next(a for a in artifacts if a.kind == "structured_evidence_bundle")
    assert bundle_artifact.metadata.get("extracted_payload_artifact_id")
    assert bundle_artifact.metadata.get("normalized_payload_artifact_id")
