from __future__ import annotations

from collections import defaultdict

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.models import ExecutionRequest, ExecutionResult, ObservationRequest, ObservationResult, VerificationRequest, VerificationResult

from .adapter import _classify_run_text


class FakeOpenHandsAdapter:
    def __init__(self, artifact_store: ArtifactStore, *, scripts: dict[str, list[str]]) -> None:
        self.artifact_store = artifact_store
        self.scripts = {key: list(value) for key, value in scripts.items()}
        self.calls: dict[str, list[object]] = defaultdict(list)

    def _next(self, kind: str) -> str:
        queue = self.scripts.get(kind)
        if not queue:
            raise RuntimeError(f"No scripted OpenHands response for {kind}")
        return queue.pop(0)

    async def observe(self, request: ObservationRequest) -> ObservationResult:
        self.calls["observe"].append(request)
        text = self._next("observe")
        transport_error, evidence_kind = _classify_run_text(text)
        artifact = self.artifact_store.add_text("observation_evidence", text, metadata={"request_id": request.id, "evidence_kind": evidence_kind})
        return ObservationResult(
            request_id=request.id,
            ok=bool(text.strip()) and not transport_error,
            summary=text[:400] if not transport_error else "OpenHands did not return usable observation evidence.",
            evidence_text=text,
            artifacts=[artifact],
            conversation_id="fake-observe",
            transport_error=transport_error,
            evidence_kind=evidence_kind,
        )

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.calls["execute"].append(request)
        text = self._next("execute")
        transport_error, evidence_kind = _classify_run_text(text)
        artifact = self.artifact_store.add_text("execution_evidence", text, metadata={"request_id": request.id, "evidence_kind": evidence_kind})
        return ExecutionResult(
            request_id=request.id,
            ok=bool(text.strip()) and not transport_error,
            summary=text[:400] if not transport_error else "OpenHands did not return usable execution evidence.",
            evidence_text=text,
            artifacts=[artifact],
            conversation_id="fake-execute",
            transport_error=transport_error,
            evidence_kind=evidence_kind,
        )

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        self.calls["verify"].append(request)
        text = self._next("verify")
        transport_error, evidence_kind = _classify_run_text(text)
        artifact = self.artifact_store.add_text("verification_evidence", text, metadata={"request_id": request.id, "evidence_kind": evidence_kind})
        passed = bool(text.strip()) and not transport_error
        return VerificationResult(
            request_id=request.id,
            passed=passed,
            summary=text[:400] if not transport_error else "OpenHands did not return usable verification evidence.",
            evidence_text=text,
            artifacts=[artifact],
            conversation_id="fake-verify",
            checks_passed=request.checks if passed else [],
            checks_failed=[] if passed else list(request.checks),
            missing_evidence=["usable verification evidence"] if transport_error else [],
            confidence="medium",
            verifier_backend="fake_openhands",
        )
