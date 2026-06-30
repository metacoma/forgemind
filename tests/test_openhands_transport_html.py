from __future__ import annotations

import pytest

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.models import BackendKind, ExecutionFamily, VerificationMode, VerificationRequest
from artifact_workflow_runtime.openhands_adapter.fake import FakeOpenHandsAdapter

pytestmark = pytest.mark.asyncio


async def test_openhands_html_response_is_transport_error_not_evidence(tmp_path) -> None:
    html = "<!DOCTYPE html><html lang='en'><head><title>OpenHands</title></head><body>SPA shell</body></html>"
    store = ArtifactStore(tmp_path)
    adapter = FakeOpenHandsAdapter(store, scripts={"verify": [html]})
    request = VerificationRequest(
        execution_result_id="exec_1",
        execution_family=ExecutionFamily.REPOSITORY_CHANGE,
        backend=BackendKind.OPENHANDS,
        mode=VerificationMode.WORLD_CHECK,
        prompt="Run integration checks only.",
        checks=["integration tests"],
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
    )

    result = await adapter.verify(request)

    assert result.passed is False
    assert result.transport_error is True
    assert result.evidence_kind == "html_transport_error"
    assert result.missing_evidence == ["usable verification evidence"]
    raw_artifact = store.get(result.raw_evidence_artifact_id or "")
    assert raw_artifact.kind == "openhands_stage_failure"
    persisted = store.read_text(raw_artifact.id)
    assert "<!DOCTYPE html>" not in persisted
    assert "<html" not in persisted.lower()
    assert "OpenHands stage failure during verification" in persisted
    assert "raw_response_persisted: false" in persisted
    assert result.evidence_bundle is not None
    assert result.evidence_bundle.structured.blockers
