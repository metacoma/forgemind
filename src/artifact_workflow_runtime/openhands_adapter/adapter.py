from __future__ import annotations

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.models import (
    ExecutionRequest,
    ExecutionResult,
    ObservationRequest,
    ObservationResult,
    VerificationRequest,
    VerificationResult,
)

from .instance import OpenHandsInstance


class OpenHandsAdapter:
    def __init__(self, instance: OpenHandsInstance, artifact_store: ArtifactStore) -> None:
        self.instance = instance
        self.artifact_store = artifact_store

    async def observe(self, request: ObservationRequest) -> ObservationResult:
        run = await self.instance.run(
            prompt=request.prompt,
            repository=request.repository,
            branch=request.branch,
            git_provider=request.git_provider,
            title=f"observe:{request.task_id}",
        )
        artifact = self.artifact_store.add_text(
            "observation_evidence",
            run.text,
            metadata={"conversation_id": run.conversation_id, "request_id": request.id},
        )
        return ObservationResult(
            request_id=request.id,
            ok=bool(run.text.strip()),
            summary=run.text.strip()[:400],
            evidence_text=run.text,
            artifacts=[artifact],
            conversation_id=run.conversation_id,
        )

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        run = await self.instance.run(
            prompt=request.prompt,
            repository=request.repository,
            branch=request.branch,
            git_provider=request.git_provider,
            title=f"execute:{request.task_id}",
        )
        artifact = self.artifact_store.add_text(
            "execution_evidence",
            run.text,
            metadata={"conversation_id": run.conversation_id, "request_id": request.id},
        )
        return ExecutionResult(
            request_id=request.id,
            ok=bool(run.text.strip()),
            summary=run.text.strip()[:400],
            evidence_text=run.text,
            artifacts=[artifact],
            conversation_id=run.conversation_id,
        )

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        run = await self.instance.run(
            prompt=request.prompt,
            repository=request.repository,
            branch=request.branch,
            git_provider=request.git_provider,
            title="verify",
        )
        text_lower = run.text.lower()
        passed = "pass" in text_lower or "ok" in text_lower or "success" in text_lower
        artifact = self.artifact_store.add_text(
            "verification_evidence",
            run.text,
            metadata={"conversation_id": run.conversation_id, "request_id": request.id},
        )
        return VerificationResult(
            request_id=request.id,
            passed=passed,
            summary=run.text.strip()[:400],
            evidence_text=run.text,
            artifacts=[artifact],
            conversation_id=run.conversation_id,
        )
