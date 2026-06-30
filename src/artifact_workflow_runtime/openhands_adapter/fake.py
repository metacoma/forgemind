from __future__ import annotations

from collections import defaultdict

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.evidence import EvidenceExtractor, render_structured_evidence_summary
from artifact_workflow_runtime.models import (
    BackendKind,
    EvidenceBundle,
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

from .adapter import (
    _add_transport_error_blocker,
    _execution_status_from_bundle,
    _verification_passed_from_bundle,
)
from .models import AppConversationStart, OpenHandsRunResult
from .result_gate import StageResultGate


class FakeOpenHandsAdapter:
    def __init__(self, artifact_store: ArtifactStore, *, scripts: dict[str, list[str]]) -> None:
        self.artifact_store = artifact_store
        self.scripts = {key: list(value) for key, value in scripts.items()}
        self.calls: dict[str, list[object]] = defaultdict(list)
        self.evidence_extractor = EvidenceExtractor()
        self.result_gate = StageResultGate(artifact_store)

    def _next(self, kind: str) -> str:
        queue = self.scripts.get(kind)
        if not queue:
            raise RuntimeError(f"No scripted OpenHands response for {kind}")
        return queue.pop(0)

    @staticmethod
    def _fake_run(*, text: str, stage: str) -> OpenHandsRunResult:
        start = AppConversationStart(conversation_id=f"fake-{stage}", status="finished")
        return OpenHandsRunResult(text=text, status="finished", conversation_id=f"fake-{stage}", start=start)

    def _materialize_stage_result(self, *, stage: str, request_id: str, work_packet_kind: WorkPacketKind, text: str):
        run = self._fake_run(text=text, stage=stage)
        gate = self.result_gate.evaluate(stage=stage, request_id=request_id, work_packet_kind=work_packet_kind, run=run)
        if gate.diagnostic_artifact is not None:
            return gate.diagnostic_artifact, gate.evidence_text, gate.ok, gate.transport_error, gate.evidence_kind, gate.summary, gate.failure
        artifact = self.artifact_store.add_text(
            gate.artifact_kind,
            gate.evidence_text,
            metadata={"request_id": request_id, "evidence_kind": gate.evidence_kind, "raw_response_persisted": True},
        )
        return artifact, gate.evidence_text, gate.ok, gate.transport_error, gate.evidence_kind, gate.summary, gate.failure

    def _bundle(self, *, text: str, raw_artifact_id: str, request_id: str, ok: bool, summary: str, evidence_kind: str, work_packet_kind: WorkPacketKind, changed_default: bool = False) -> tuple[EvidenceBundle, object]:
        structured = self.evidence_extractor.from_agent_output(text, artifact_id=raw_artifact_id, changed_default=changed_default)
        structured = _add_transport_error_blocker(structured, evidence_kind=evidence_kind)
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
        artifact, evidence_text, ok, transport_error, evidence_kind, summary, stage_failure = self._materialize_stage_result(
            stage="observation",
            request_id=request.id,
            work_packet_kind=request.work_packet_kind,
            text=self._next("observe"),
        )
        bundle, bundle_artifact = self._bundle(text=evidence_text, raw_artifact_id=artifact.id, request_id=request.id, ok=ok, summary=summary, evidence_kind=evidence_kind, work_packet_kind=request.work_packet_kind)
        return ObservationResult(
            request_id=request.id,
            ok=ok,
            summary=summary,
            evidence_text=evidence_text,
            artifacts=[artifact, bundle_artifact],
            structured_evidence=bundle.structured,
            evidence_bundle=bundle,
            primary_evidence_artifact_ids=[bundle_artifact.id],
            raw_evidence_artifact_id=artifact.id,
            conversation_id="fake-observe",
            transport_error=transport_error,
            evidence_kind=evidence_kind,
            stage_failure=stage_failure,
        )

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.calls["execute"].append(request)
        artifact, evidence_text, ok, transport_error, evidence_kind, summary, stage_failure = self._materialize_stage_result(
            stage="execution",
            request_id=request.id,
            work_packet_kind=request.work_packet_kind,
            text=self._next("execute"),
        )
        bundle, bundle_artifact = self._bundle(text=evidence_text, raw_artifact_id=artifact.id, request_id=request.id, ok=ok, summary=summary, evidence_kind=evidence_kind, work_packet_kind=request.work_packet_kind, changed_default=False)
        return ExecutionResult(
            request_id=request.id,
            ok=ok,
            execution_status=_execution_status_from_bundle(ok=ok, transport_error=transport_error, bundle=bundle),
            summary=summary,
            evidence_text=evidence_text,
            artifacts=[artifact, bundle_artifact],
            structured_evidence=bundle.structured,
            evidence_bundle=bundle,
            primary_evidence_artifact_ids=[bundle_artifact.id],
            raw_evidence_artifact_id=artifact.id,
            conversation_id="fake-execute",
            transport_error=transport_error,
            evidence_kind=evidence_kind,
            stage_failure=stage_failure,
        )

    async def publish(self, request: PublishRequest) -> PublishResult:
        self.calls["publish"].append(request)
        artifact, evidence_text, ok, transport_error, evidence_kind, summary, stage_failure = self._materialize_stage_result(
            stage="publish",
            request_id=request.id,
            work_packet_kind=WorkPacketKind.PUBLISH,
            text=self._next("publish"),
        )
        bundle, bundle_artifact = self._bundle(text=evidence_text, raw_artifact_id=artifact.id, request_id=request.id, ok=ok, summary=summary, evidence_kind=evidence_kind, work_packet_kind=WorkPacketKind.PUBLISH)
        return PublishResult(
            request_id=request.id,
            ok=ok,
            summary=summary,
            evidence_text=evidence_text,
            artifacts=[artifact, bundle_artifact],
            structured_evidence=bundle.structured,
            evidence_bundle=bundle,
            primary_evidence_artifact_ids=[bundle_artifact.id],
            raw_evidence_artifact_id=artifact.id,
            conversation_id="fake-publish",
            transport_error=transport_error,
            evidence_kind=evidence_kind,
            stage_failure=stage_failure,
        )

    async def repair(self, request: RepairRequest) -> RepairResult:
        self.calls["repair"].append(request)
        artifact, evidence_text, ok, transport_error, evidence_kind, summary, stage_failure = self._materialize_stage_result(
            stage="repair",
            request_id=request.id,
            work_packet_kind=WorkPacketKind.REPAIR,
            text=self._next("repair"),
        )
        bundle, bundle_artifact = self._bundle(text=evidence_text, raw_artifact_id=artifact.id, request_id=request.id, ok=ok, summary=summary, evidence_kind=evidence_kind, work_packet_kind=WorkPacketKind.REPAIR, changed_default=False)
        execution_result = ExecutionResult(
            request_id=request.id,
            ok=ok,
            execution_status=_execution_status_from_bundle(ok=ok, transport_error=transport_error, bundle=bundle),
            summary=summary,
            evidence_text=evidence_text,
            artifacts=[artifact, bundle_artifact],
            structured_evidence=bundle.structured,
            evidence_bundle=bundle,
            primary_evidence_artifact_ids=[bundle_artifact.id],
            raw_evidence_artifact_id=artifact.id,
            conversation_id="fake-repair",
            transport_error=transport_error,
            evidence_kind=evidence_kind,
            stage_failure=stage_failure,
        )
        return RepairResult(request_id=request.id, attempt=request.attempt, ok=ok, summary=summary, execution_result=execution_result)

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        self.calls["verify"].append(request)
        artifact, evidence_text, ok, transport_error, evidence_kind, summary, stage_failure = self._materialize_stage_result(
            stage="verification",
            request_id=request.id,
            work_packet_kind=WorkPacketKind.VERIFY,
            text=self._next("verify"),
        )
        bundle, bundle_artifact = self._bundle(text=evidence_text, raw_artifact_id=artifact.id, request_id=request.id, ok=ok, summary=summary, evidence_kind=evidence_kind, work_packet_kind=WorkPacketKind.VERIFY)
        passed = _verification_passed_from_bundle(text=evidence_text, ok=ok, transport_error=transport_error, bundle=bundle)
        return VerificationResult(
            request_id=request.id,
            passed=passed,
            summary=summary,
            evidence_text=evidence_text,
            artifacts=[artifact, bundle_artifact],
            structured_evidence=bundle.structured,
            evidence_bundle=bundle,
            primary_evidence_artifact_ids=[bundle_artifact.id],
            raw_evidence_artifact_id=artifact.id,
            conversation_id="fake-verify",
            transport_error=transport_error,
            evidence_kind=evidence_kind,
            stage_failure=stage_failure,
            checks_passed=request.checks if passed else [],
            checks_failed=[] if passed else list(request.checks),
            missing_evidence=["usable verification evidence"] if stage_failure is not None else [],
            confidence="medium",
            verifier_backend="fake_openhands",
            mode=request.mode if request.mode else VerificationMode.WORLD_CHECK,
        )
