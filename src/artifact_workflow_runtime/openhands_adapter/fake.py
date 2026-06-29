from __future__ import annotations

from collections import defaultdict

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.models import ExecutionRequest, ExecutionResult, ObservationRequest, ObservationResult, VerificationRequest, VerificationResult


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
        artifact = self.artifact_store.add_text("observation_evidence", text, metadata={"request_id": request.id})
        return ObservationResult(request_id=request.id, ok=True, summary=text[:400], evidence_text=text, artifacts=[artifact], conversation_id="fake-observe")

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.calls["execute"].append(request)
        text = self._next("execute")
        artifact = self.artifact_store.add_text("execution_evidence", text, metadata={"request_id": request.id})
        return ExecutionResult(request_id=request.id, ok=True, summary=text[:400], evidence_text=text, artifacts=[artifact], conversation_id="fake-execute")

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        self.calls["verify"].append(request)
        text = self._next("verify")
        artifact = self.artifact_store.add_text("verification_evidence", text, metadata={"request_id": request.id})
        return VerificationResult(request_id=request.id, passed=True, summary=text[:400], evidence_text=text, artifacts=[artifact], conversation_id="fake-verify")
