from __future__ import annotations

from collections import defaultdict

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.evidence import EvidenceExtractor
from artifact_workflow_runtime.models import (
    BackendKind,
    EvidenceBundle,
    ExecutionRequest,
    ExecutionResult,
    ObservationRequest,
    ObservationResult,
    VerificationMode,
    VerificationRequest,
    VerificationResult,
    WorkPacketKind,
)

from .adapter import _classify_run_text


class FakeOpenHandsAdapter:
    def __init__(self, artifact_store: ArtifactStore, *, scripts: dict[str, list[str]]) -> None:
        self.artifact_store = artifact_store
        self.scripts = {key: list(value) for key, value in scripts.items()}
        self.calls: dict[str, list[object]] = defaultdict(list)
        self.evidence_extractor = EvidenceExtractor()

    def _next(self, kind: str) -> str:
        queue = self.scripts.get(kind)
        if not queue:
            raise RuntimeError(f"No scripted OpenHands response for {kind}")
        return queue.pop(0)

    def _bundle(self, *, text: str, raw_artifact_id: str, request_id: str, ok: bool, summary: str, evidence_kind: str, work_packet_kind: WorkPacketKind, changed_default: bool = False) -> tuple[EvidenceBundle, object]:
        structured = self.evidence_extractor.from_text(text, artifact_id=raw_artifact_id, changed_default=changed_default)
        bundle = EvidenceBundle(
            source_backend=BackendKind.OPENHANDS,
            work_packet_kind=work_packet_kind,
            ok=ok,
            summary=summary,
            artifact_ids=[raw_artifact_id],
            structured=structured,
            evidence_kind=evidence_kind,
            blockers=[item.summary for item in structured.blockers],
        )
        artifact = self.artifact_store.add_json(
            "structured_evidence_bundle",
            bundle.model_dump(mode="json"),
            metadata={"request_id": request_id, "work_packet_kind": work_packet_kind.value, "backend": BackendKind.OPENHANDS.value},
        )
        bundle.artifact_ids.append(artifact.id)
        return bundle, artifact

    async def observe(self, request: ObservationRequest) -> ObservationResult:
        self.calls["observe"].append(request)
        text = self._next("observe")
        transport_error, evidence_kind = _classify_run_text(text)
        ok = bool(text.strip()) and not transport_error
        artifact = self.artifact_store.add_text("observation_evidence", text, metadata={"request_id": request.id, "evidence_kind": evidence_kind})
        summary = text[:400] if not transport_error else "OpenHands did not return usable observation evidence."
        bundle, bundle_artifact = self._bundle(
            text=text,
            raw_artifact_id=artifact.id,
            request_id=request.id,
            ok=ok,
            summary=summary,
            evidence_kind=evidence_kind,
            work_packet_kind=request.work_packet_kind,
        )
        return ObservationResult(
            request_id=request.id,
            ok=ok,
            summary=summary,
            evidence_text=text,
            artifacts=[artifact, bundle_artifact],
            structured_evidence=bundle.structured,
            evidence_bundle=bundle,
            conversation_id="fake-observe",
            transport_error=transport_error,
            evidence_kind=evidence_kind,
        )

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.calls["execute"].append(request)
        text = self._next("execute")
        transport_error, evidence_kind = _classify_run_text(text)
        ok = bool(text.strip()) and not transport_error
        artifact = self.artifact_store.add_text("execution_evidence", text, metadata={"request_id": request.id, "evidence_kind": evidence_kind})
        summary = text[:400] if not transport_error else "OpenHands did not return usable execution evidence."
        bundle, bundle_artifact = self._bundle(
            text=text,
            raw_artifact_id=artifact.id,
            request_id=request.id,
            ok=ok,
            summary=summary,
            evidence_kind=evidence_kind,
            work_packet_kind=request.work_packet_kind,
            changed_default=request.work_packet_kind == WorkPacketKind.EXECUTE,
        )
        return ExecutionResult(
            request_id=request.id,
            ok=ok,
            summary=summary,
            evidence_text=text,
            artifacts=[artifact, bundle_artifact],
            structured_evidence=bundle.structured,
            evidence_bundle=bundle,
            conversation_id="fake-execute",
            transport_error=transport_error,
            evidence_kind=evidence_kind,
        )

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        self.calls["verify"].append(request)
        text = self._next("verify")
        transport_error, evidence_kind = _classify_run_text(text)
        ok = bool(text.strip()) and not transport_error
        artifact = self.artifact_store.add_text("verification_evidence", text, metadata={"request_id": request.id, "evidence_kind": evidence_kind})
        summary = text[:400] if not transport_error else "OpenHands did not return usable verification evidence."
        bundle, bundle_artifact = self._bundle(
            text=text,
            raw_artifact_id=artifact.id,
            request_id=request.id,
            ok=ok,
            summary=summary,
            evidence_kind=evidence_kind,
            work_packet_kind=WorkPacketKind.VERIFY,
        )
        passed = ok
        return VerificationResult(
            request_id=request.id,
            passed=passed,
            summary=summary,
            evidence_text=text,
            artifacts=[artifact, bundle_artifact],
            structured_evidence=bundle.structured,
            evidence_bundle=bundle,
            conversation_id="fake-verify",
            checks_passed=request.checks if passed else [],
            checks_failed=[] if passed else list(request.checks),
            missing_evidence=["usable verification evidence"] if transport_error else [],
            confidence="medium",
            verifier_backend="fake_openhands",
            mode=request.mode if request.mode else VerificationMode.WORLD_CHECK,
        )
