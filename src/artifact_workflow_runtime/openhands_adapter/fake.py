from __future__ import annotations

from collections import defaultdict

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.evidence import EvidenceExtractor, render_structured_evidence_summary
from artifact_workflow_runtime.models import (
    BackendKind,
    EvidenceBundle,
    BlockerKind,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    ObservationRequest,
    ObservationResult,
    PublishRequest,
    PublishResult,
    RepairRequest,
    RepairResult,
    VerificationMode,
    VerificationRequest,
    VerificationResult,
    WorkPacketKind,
)

from .adapter import _classify_run_text, _execution_status_from_bundle, _verification_passed_from_bundle


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
        structured = self.evidence_extractor.from_agent_output(text, artifact_id=raw_artifact_id, changed_default=changed_default)
        bundle = EvidenceBundle(
            source_backend=BackendKind.OPENHANDS,
            work_packet_kind=work_packet_kind,
            ok=ok,
            summary=summary,
            artifact_ids=[raw_artifact_id],
            structured=structured,
            evidence_kind=evidence_kind,
            raw_text_artifact_id=raw_artifact_id,
            blockers=[item.summary for item in structured.blockers],
        )
        artifact = self.artifact_store.add_json(
            "structured_evidence_bundle",
            bundle.model_dump(mode="json"),
            metadata={"request_id": request_id, "work_packet_kind": work_packet_kind.value, "backend": BackendKind.OPENHANDS.value},
        )
        bundle.artifact_ids.append(artifact.id)
        bundle.structured_artifact_id = artifact.id
        bundle.summary = render_structured_evidence_summary(bundle.structured) or bundle.summary
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
            primary_evidence_artifact_ids=[bundle_artifact.id],
            raw_evidence_artifact_id=artifact.id,
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
            changed_default=False,
        )
        return ExecutionResult(
            request_id=request.id,
            ok=ok,
            execution_status=_execution_status_from_bundle(ok=ok, transport_error=transport_error, bundle=bundle),
            summary=summary,
            evidence_text=text,
            artifacts=[artifact, bundle_artifact],
            structured_evidence=bundle.structured,
            evidence_bundle=bundle,
            primary_evidence_artifact_ids=[bundle_artifact.id],
            raw_evidence_artifact_id=artifact.id,
            conversation_id="fake-execute",
            transport_error=transport_error,
            evidence_kind=evidence_kind,
        )

    async def publish(self, request: PublishRequest) -> PublishResult:
        self.calls["publish"].append(request)
        text = self._next("publish")
        transport_error, evidence_kind = _classify_run_text(text)
        ok = bool(text.strip()) and not transport_error
        artifact = self.artifact_store.add_text("publish_evidence", text, metadata={"request_id": request.id, "evidence_kind": evidence_kind})
        summary = text[:400] if not transport_error else "OpenHands did not return usable publish evidence."
        bundle, bundle_artifact = self._bundle(
            text=text,
            raw_artifact_id=artifact.id,
            request_id=request.id,
            ok=ok,
            summary=summary,
            evidence_kind=evidence_kind,
            work_packet_kind=WorkPacketKind.PUBLISH,
        )
        return PublishResult(
            request_id=request.id,
            ok=ok,
            summary=summary,
            evidence_text=text,
            artifacts=[artifact, bundle_artifact],
            structured_evidence=bundle.structured,
            evidence_bundle=bundle,
            primary_evidence_artifact_ids=[bundle_artifact.id],
            raw_evidence_artifact_id=artifact.id,
            conversation_id="fake-publish",
            transport_error=transport_error,
            evidence_kind=evidence_kind,
        )


    async def repair(self, request: RepairRequest) -> RepairResult:
        self.calls["repair"].append(request)
        text = self._next("repair")
        transport_error, evidence_kind = _classify_run_text(text)
        ok = bool(text.strip()) and not transport_error
        artifact = self.artifact_store.add_text("repair_evidence", text, metadata={"request_id": request.id, "evidence_kind": evidence_kind, "attempt": request.attempt})
        summary = text[:400] if not transport_error else "OpenHands did not return usable repair evidence."
        bundle, bundle_artifact = self._bundle(
            text=text,
            raw_artifact_id=artifact.id,
            request_id=request.id,
            ok=ok,
            summary=summary,
            evidence_kind=evidence_kind,
            work_packet_kind=WorkPacketKind.REPAIR,
            changed_default=False,
        )
        execution_result = ExecutionResult(
            request_id=request.id,
            ok=ok,
            execution_status=_execution_status_from_bundle(ok=ok, transport_error=transport_error, bundle=bundle),
            summary=summary,
            evidence_text=text,
            artifacts=[artifact, bundle_artifact],
            structured_evidence=bundle.structured,
            evidence_bundle=bundle,
            primary_evidence_artifact_ids=[bundle_artifact.id],
            raw_evidence_artifact_id=artifact.id,
            conversation_id="fake-repair",
            transport_error=transport_error,
            evidence_kind=evidence_kind,
        )
        return RepairResult(request_id=request.id, attempt=request.attempt, ok=ok, summary=summary, execution_result=execution_result)

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
        passed = _verification_passed_from_bundle(text=text, ok=ok, transport_error=transport_error, bundle=bundle)
        return VerificationResult(
            request_id=request.id,
            passed=passed,
            summary=summary,
            evidence_text=text,
            artifacts=[artifact, bundle_artifact],
            structured_evidence=bundle.structured,
            evidence_bundle=bundle,
            primary_evidence_artifact_ids=[bundle_artifact.id],
            raw_evidence_artifact_id=artifact.id,
            conversation_id="fake-verify",
            checks_passed=request.checks if passed else [],
            checks_failed=[] if passed else list(request.checks),
            missing_evidence=["usable verification evidence"] if transport_error else [],
            confidence="medium",
            verifier_backend="fake_openhands",
            mode=request.mode if request.mode else VerificationMode.WORLD_CHECK,
        )
