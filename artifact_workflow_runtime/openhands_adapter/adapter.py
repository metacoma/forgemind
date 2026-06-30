from __future__ import annotations

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.evidence import EvidenceContractError, EvidenceExtractor, render_structured_evidence_summary
from artifact_workflow_runtime.model_routing import ModelRoutingConfig
from artifact_workflow_runtime.models import (
    BackendKind,
    EvidenceBundle,
    BlockerEvidence,
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
    StructuredResponseContract,
    VerificationMode,
    VerificationRequest,
    VerificationResult,
    StructuredEvidence,
    StageFailureKind,
    OpenHandsRunFailure,
    WorkPacketKind,
)

from .contracts import OpenHandsStageContractGate
from .instance import OpenHandsInstance
from .result_gate import StageResultGate




def _add_transport_error_blocker(structured: StructuredEvidence, *, evidence_kind: str) -> StructuredEvidence:
    if evidence_kind == "agent_text":
        return structured
    structured.blockers.append(
        BlockerEvidence(
            summary=f"OpenHands transport did not return usable agent evidence: {evidence_kind}",
            severity="high",
            blocker_kind=BlockerKind.EXECUTION_FAILURE,
        )
    )
    return structured


def _execution_status_from_bundle(*, ok: bool, transport_error: bool, bundle: EvidenceBundle) -> ExecutionStatus:
    if not ok:
        return ExecutionStatus.FAILED if transport_error else ExecutionStatus.BLOCKED
    has_mutation = bool(bundle.structured.mutation_summary.changed or bundle.structured.files_changed)
    env_blocked = any(
        item.blocker_kind in {BlockerKind.MISSING_ENVIRONMENT_DEPENDENCY, BlockerKind.MISSING_RUNTIME_PREREQUISITE, BlockerKind.INTEGRATION_ENVIRONMENT_UNAVAILABLE}
        for item in bundle.structured.blockers
    )
    if env_blocked or bundle.structured.blockers:
        return ExecutionStatus.PARTIAL if has_mutation else ExecutionStatus.BLOCKED
    return ExecutionStatus.SUCCEEDED


def _verification_passed_from_bundle(*, text: str, ok: bool, transport_error: bool, bundle: EvidenceBundle) -> bool:
    if not ok or transport_error:
        return False
    if bundle.structured.blockers:
        return False
    if bundle.structured.tests:
        statuses = {str(item.status).lower() for item in bundle.structured.tests}
        return bool(statuses) and statuses <= {"passed", "success", "succeeded", "ok"}
    lowered = text.lower()
    negative = ("not run", "not executed", "missing", "blocked", "failed", "failure", "error", "unable", "cannot")
    positive = ("passed", "success", "successful", "ok", "green")
    return any(marker in lowered for marker in positive) and not any(marker in lowered for marker in negative)


def _contract_repair_prompt(*, stage: str, response_contract: StructuredResponseContract, evidence_requirements: object | None) -> str:
    lines = [
        f"Return JSON only. This is the machine-readable handoff for the {stage} stage.",
        "Do not perform any new repository, shell, git, network, or environment actions.",
        "Do not edit files, run commands, rerun tests, install dependencies, commit, push, create PRs, or change workflow decisions.",
        "Use only the work and observations that already happened in this conversation.",
        "Do not include prose before or after the JSON.",
        "Do not include markdown fences.",
        "Do not save a file or use MCP/file-save tools.",
        "If a required field has no data, return an empty value of the appropriate type and explain the gap in blockers.",
        "Desired JSON contract:",
        response_contract.render(),
    ]
    if evidence_requirements is not None:
        render = getattr(evidence_requirements, "render", None)
        if callable(render):
            lines.extend(["Operational evidence requirements:", render()])
    return "\n".join(lines)


def _response_contract_for_request(request: object) -> StructuredResponseContract | None:
    contract = getattr(request, "response_contract", None)
    return contract if isinstance(contract, StructuredResponseContract) else None



class OpenHandsAdapter:
    def __init__(
        self,
        instance: OpenHandsInstance,
        artifact_store: ArtifactStore,
        model_routing: ModelRoutingConfig | None = None,
        *,
        strict_evidence: bool = True,
    ) -> None:
        self.instance = instance
        self.artifact_store = artifact_store
        self.model_routing = model_routing
        self.strict_evidence = strict_evidence
        self.evidence_extractor = EvidenceExtractor()
        self.contract_gate = OpenHandsStageContractGate()
        self.result_gate = StageResultGate(artifact_store)

    async def _run_json_handoff_followup(
        self,
        *,
        stage: str,
        request: object,
        run,
    ) -> tuple[object | None, OpenHandsRunFailure | None, str | None]:
        response_contract = _response_contract_for_request(request)
        followup = getattr(self.instance, "followup", None)
        if response_contract is None or followup is None or getattr(run, "start", None) is None:
            return None, None, None
        repair_prompt = _contract_repair_prompt(
            stage=stage,
            response_contract=response_contract,
            evidence_requirements=getattr(request, "evidence_requirements", None),
        )
        try:
            return await followup(conversation=run, prompt=repair_prompt), None, None
        except Exception as exc:  # pragma: no cover - defensive runtime guard
            message = f"OpenHands JSON handoff follow-up failed: {exc}"
            failure = OpenHandsRunFailure(
                stage=stage,
                request_id=getattr(request, "id", ""),
                work_packet_kind=getattr(request, "work_packet_kind", WorkPacketKind.EXECUTE),
                failure_kind=StageFailureKind.API_ERROR,
                summary=message,
                retryable=True,
                evidence_kind="api_error",
                conversation_id=getattr(getattr(run, "start", None), "conversation_id", None),
                sandbox_id=getattr(getattr(run, "start", None), "sandbox_id", None),
            )
            return None, failure, message

    async def _bundle_with_json_handoff(
        self,
        *,
        stage: str,
        request: object,
        run,
        artifact,
        evidence_text: str,
        ok: bool,
        transport_error: bool,
        evidence_kind: str,
        summary: str,
        stage_failure,
        work_packet_kind: WorkPacketKind,
        changed_default: bool = False,
    ):
        artifacts = [artifact]
        selected_run = run
        selected_artifact = artifact
        selected_text = evidence_text
        selected_ok = ok
        selected_transport_error = transport_error
        selected_evidence_kind = evidence_kind
        selected_summary = summary
        selected_stage_failure = stage_failure

        if not transport_error and str(evidence_text or "").strip():
            handoff_run, handoff_failure, handoff_error_text = await self._run_json_handoff_followup(stage=stage, request=request, run=run)
            if handoff_run is not None:
                handoff_artifact, handoff_text, handoff_ok, handoff_transport_error, handoff_evidence_kind, handoff_summary, handoff_stage_failure = self._materialize_stage_result(
                    stage=stage,
                    request_id=request.id,
                    work_packet_kind=work_packet_kind,
                    run=handoff_run,
                )
                artifacts.append(handoff_artifact)
                selected_run = handoff_run
                selected_artifact = handoff_artifact
                selected_text = handoff_text
                selected_ok = handoff_ok
                selected_transport_error = handoff_transport_error
                selected_evidence_kind = handoff_evidence_kind
                selected_summary = handoff_summary
                selected_stage_failure = handoff_stage_failure
            elif handoff_failure is not None and handoff_error_text is not None:
                failure_artifact = self.artifact_store.add_text(
                    "openhands_followup_error",
                    handoff_error_text,
                    metadata={
                        "conversation_id": getattr(getattr(run, "start", None), "conversation_id", None),
                        "request_id": getattr(request, "id", None),
                        "evidence_kind": "api_error",
                        "raw_response_persisted": True,
                    },
                )
                artifacts.append(failure_artifact)
                selected_artifact = failure_artifact
                selected_text = handoff_error_text
                selected_ok = False
                selected_transport_error = True
                selected_evidence_kind = "api_error"
                selected_summary = handoff_failure.summary
                selected_stage_failure = handoff_failure

        bundle, bundle_artifact, selected_ok, selected_evidence_kind, strict_failure = self._evidence_bundle(
            stage=stage,
            text=selected_text,
            raw_artifact_id=selected_artifact.id,
            request_id=request.id,
            ok=selected_ok,
            summary=selected_summary,
            evidence_kind=selected_evidence_kind,
            work_packet_kind=work_packet_kind,
            changed_default=changed_default,
        )
        artifacts.append(bundle_artifact)
        effective_stage_failure = selected_stage_failure or strict_failure
        return artifacts, bundle, selected_ok, selected_evidence_kind, selected_transport_error, selected_summary, effective_stage_failure, selected_text, selected_run, selected_artifact

    def _resolve_stage_model(self, metadata: dict[str, object] | None, fallback_slot: str) -> str | None:
        metadata = metadata or {}
        explicit = metadata.get("model_override")
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip()
        slot_obj = metadata.get("model_slot")
        slot = str(slot_obj).strip() if isinstance(slot_obj, str) and slot_obj.strip() else fallback_slot
        default_model = getattr(self.instance, "default_model", None)
        return self.model_routing.resolve_openhands(slot, default_model) if self.model_routing else default_model

    @staticmethod
    def _forbidden_set(request: object) -> set[str]:
        return OpenHandsStageContractGate.forbidden_set(request)

    @staticmethod
    def _allowed_set(request: object) -> set[str]:
        return OpenHandsStageContractGate.allowed_set(request)

    @staticmethod
    def _require_forbidden_actions(request: object, required: set[str], label: str) -> None:
        OpenHandsStageContractGate.require_forbidden_actions(request, required, label)

    @staticmethod
    def _validate_compiled_prompt_contains_contract(request: object, *, label: str) -> None:
        OpenHandsStageContractGate.validate_compiled_prompt_contains_contract(request, label=label)

    @staticmethod
    def _validate_observation_contract(request: ObservationRequest) -> None:
        OpenHandsStageContractGate.validate_observation(request)

    @staticmethod
    def _validate_execution_contract(request: ExecutionRequest) -> None:
        OpenHandsStageContractGate.validate_execution(request)


    @staticmethod
    def _validate_publish_contract(request: PublishRequest) -> None:
        OpenHandsStageContractGate.validate_publish(request)


    @staticmethod
    def _validate_repair_contract(request: RepairRequest) -> None:
        OpenHandsStageContractGate.validate_repair(request)

    @staticmethod
    def _validate_world_verification_contract(request: VerificationRequest) -> None:
        OpenHandsStageContractGate.validate_world_verification(request)

    def _materialize_stage_result(self, *, stage: str, request_id: str, work_packet_kind: WorkPacketKind, run) -> tuple[object, str, bool, bool, str, str, object | None]:
        gate = self.result_gate.evaluate(stage=stage, request_id=request_id, work_packet_kind=work_packet_kind, run=run)
        if gate.diagnostic_artifact is not None:
            return gate.diagnostic_artifact, gate.evidence_text, gate.ok, gate.transport_error, gate.evidence_kind, gate.summary, gate.failure
        artifact = self.artifact_store.add_text(
            gate.artifact_kind,
            gate.evidence_text,
            metadata={
                "conversation_id": run.conversation_id,
                "request_id": request_id,
                "evidence_kind": gate.evidence_kind,
                "raw_response_persisted": True,
            },
        )
        return artifact, gate.evidence_text, gate.ok, gate.transport_error, gate.evidence_kind, gate.summary, gate.failure

    def _evidence_bundle(
        self,
        *,
        stage: str,
        text: str,
        raw_artifact_id: str,
        request_id: str,
        ok: bool,
        summary: str,
        evidence_kind: str,
        work_packet_kind: WorkPacketKind,
        changed_default: bool = False,
    ) -> tuple[EvidenceBundle, object, bool, str, OpenHandsRunFailure | None]:
        strict_failure: OpenHandsRunFailure | None = None
        effective_ok = ok
        effective_evidence_kind = evidence_kind
        try:
            structured = self.evidence_extractor.from_agent_output(
                text,
                artifact_id=raw_artifact_id,
                changed_default=changed_default,
                strict=self.strict_evidence,
            )
        except EvidenceContractError as exc:
            effective_ok = False
            effective_evidence_kind = "evidence_contract_missing"
            structured = StructuredEvidence(
                blockers=[
                    BlockerEvidence(
                        summary=str(exc),
                        severity="high",
                        blocker_kind=BlockerKind.MISSING_EVIDENCE,
                        artifact_ids=[raw_artifact_id],
                    )
                ]
            )
            strict_failure = OpenHandsRunFailure(
                stage=stage,
                request_id=request_id,
                work_packet_kind=work_packet_kind,
                failure_kind=StageFailureKind.EVIDENCE_CONTRACT_MISSING,
                summary=str(exc),
                retryable=True,
                evidence_kind=effective_evidence_kind,
                diagnostic_artifact_id=raw_artifact_id,
            )
        structured = _add_transport_error_blocker(structured, evidence_kind=effective_evidence_kind)
        bundle = EvidenceBundle(
            source_backend=BackendKind.OPENHANDS,
            work_packet_kind=work_packet_kind,
            ok=effective_ok,
            summary=summary,
            artifact_ids=[raw_artifact_id],
            structured=structured,
            evidence_kind=effective_evidence_kind,
            raw_text_artifact_id=raw_artifact_id,
            blockers=[item.summary for item in structured.blockers],
        )
        artifact = self.artifact_store.add_json(
            "structured_evidence_bundle",
            bundle.model_dump(mode="json"),
            metadata={"request_id": request_id, "work_packet_kind": work_packet_kind.value, "backend": BackendKind.OPENHANDS.value, "strict_evidence": self.strict_evidence},
        )
        bundle.artifact_ids.append(artifact.id)
        bundle.structured_artifact_id = artifact.id
        bundle.summary = render_structured_evidence_summary(bundle.structured) or bundle.summary
        return bundle, artifact, effective_ok, effective_evidence_kind, strict_failure

    async def observe(self, request: ObservationRequest) -> ObservationResult:
        self._validate_observation_contract(request)
        slot = "research" if request.work_packet_kind == WorkPacketKind.RESEARCH or request.metadata.get("source") == "fresh_external_research" else "observe"
        run = await self.instance.run(
            prompt=request.compiled_prompt(),
            model=self._resolve_stage_model(request.metadata, slot),
            title=f"observe:{request.task_id}",
        )
        artifact, evidence_text, ok, transport_error, evidence_kind, summary, stage_failure = self._materialize_stage_result(
            stage="observation",
            request_id=request.id,
            work_packet_kind=request.work_packet_kind,
            run=run,
        )
        artifacts, bundle, ok, evidence_kind, transport_error, summary, effective_stage_failure, evidence_text, run, selected_artifact = await self._bundle_with_json_handoff(
            stage="observation",
            request=request,
            run=run,
            artifact=artifact,
            evidence_text=evidence_text,
            ok=ok,
            transport_error=transport_error,
            evidence_kind=evidence_kind,
            summary=summary,
            stage_failure=stage_failure,
            work_packet_kind=request.work_packet_kind,
        )
        return ObservationResult(
            request_id=request.id,
            ok=ok,
            summary=summary,
            evidence_text=evidence_text,
            artifacts=artifacts,
            structured_evidence=bundle.structured,
            evidence_bundle=bundle,
            primary_evidence_artifact_ids=[bundle.structured_artifact_id] if bundle.structured_artifact_id else [],
            raw_evidence_artifact_id=selected_artifact.id,
            conversation_id=run.conversation_id,
            transport_error=transport_error,
            evidence_kind=evidence_kind,
            stage_failure=effective_stage_failure,
        )

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self._validate_execution_contract(request)
        slot = "execute"
        run = await self.instance.run(
            prompt=request.compiled_prompt(),
            model=self._resolve_stage_model(request.metadata, slot),
            title=f"execute:{request.task_id}",
        )
        artifact, evidence_text, ok, transport_error, evidence_kind, summary, stage_failure = self._materialize_stage_result(
            stage="execution",
            request_id=request.id,
            work_packet_kind=request.work_packet_kind,
            run=run,
        )
        artifacts, bundle, ok, evidence_kind, transport_error, summary, effective_stage_failure, evidence_text, run, selected_artifact = await self._bundle_with_json_handoff(
            stage="execution",
            request=request,
            run=run,
            artifact=artifact,
            evidence_text=evidence_text,
            ok=ok,
            transport_error=transport_error,
            evidence_kind=evidence_kind,
            summary=summary,
            stage_failure=stage_failure,
            work_packet_kind=request.work_packet_kind,
            changed_default=False,
        )
        return ExecutionResult(
            request_id=request.id,
            ok=ok,
            execution_status=_execution_status_from_bundle(ok=ok, transport_error=transport_error, bundle=bundle),
            summary=summary,
            evidence_text=evidence_text,
            artifacts=artifacts,
            structured_evidence=bundle.structured,
            evidence_bundle=bundle,
            primary_evidence_artifact_ids=[bundle.structured_artifact_id] if bundle.structured_artifact_id else [],
            raw_evidence_artifact_id=selected_artifact.id,
            conversation_id=run.conversation_id,
            transport_error=transport_error,
            evidence_kind=evidence_kind,
            stage_failure=effective_stage_failure,
        )

    async def publish(self, request: PublishRequest) -> PublishResult:
        self._validate_publish_contract(request)
        run = await self.instance.run(
            prompt=request.compiled_prompt(),
            model=self._resolve_stage_model(request.metadata, "publish"),
            title=f"publish:{request.task_id}",
        )
        artifact, evidence_text, ok, transport_error, evidence_kind, summary, stage_failure = self._materialize_stage_result(
            stage="publish",
            request_id=request.id,
            work_packet_kind=request.work_packet_kind,
            run=run,
        )
        artifacts, bundle, ok, evidence_kind, transport_error, summary, effective_stage_failure, evidence_text, run, selected_artifact = await self._bundle_with_json_handoff(
            stage="publish",
            request=request,
            run=run,
            artifact=artifact,
            evidence_text=evidence_text,
            ok=ok,
            transport_error=transport_error,
            evidence_kind=evidence_kind,
            summary=summary,
            stage_failure=stage_failure,
            work_packet_kind=WorkPacketKind.PUBLISH,
        )
        return PublishResult(
            request_id=request.id,
            ok=ok,
            summary=summary,
            evidence_text=evidence_text,
            artifacts=artifacts,
            structured_evidence=bundle.structured,
            evidence_bundle=bundle,
            primary_evidence_artifact_ids=[bundle.structured_artifact_id] if bundle.structured_artifact_id else [],
            raw_evidence_artifact_id=selected_artifact.id,
            conversation_id=run.conversation_id,
            transport_error=transport_error,
            evidence_kind=evidence_kind,
            stage_failure=effective_stage_failure,
        )


    async def repair(self, request: RepairRequest) -> RepairResult:
        self._validate_repair_contract(request)
        run = await self.instance.run(
            prompt=request.compiled_prompt(),
            model=self._resolve_stage_model(request.metadata, "execute"),
            title=f"repair:{request.task_id}:attempt-{request.attempt}",
        )
        artifact, evidence_text, ok, transport_error, evidence_kind, summary, stage_failure = self._materialize_stage_result(
            stage="repair",
            request_id=request.id,
            work_packet_kind=request.work_packet_kind,
            run=run,
        )
        artifacts, bundle, ok, evidence_kind, transport_error, summary, effective_stage_failure, evidence_text, run, selected_artifact = await self._bundle_with_json_handoff(
            stage="repair",
            request=request,
            run=run,
            artifact=artifact,
            evidence_text=evidence_text,
            ok=ok,
            transport_error=transport_error,
            evidence_kind=evidence_kind,
            summary=summary,
            stage_failure=stage_failure,
            work_packet_kind=WorkPacketKind.REPAIR,
            changed_default=False,
        )
        execution_result = ExecutionResult(
            request_id=request.id,
            ok=ok,
            execution_status=_execution_status_from_bundle(ok=ok, transport_error=transport_error, bundle=bundle),
            summary=summary,
            evidence_text=evidence_text,
            artifacts=artifacts,
            structured_evidence=bundle.structured,
            evidence_bundle=bundle,
            primary_evidence_artifact_ids=[bundle.structured_artifact_id] if bundle.structured_artifact_id else [],
            raw_evidence_artifact_id=selected_artifact.id,
            conversation_id=run.conversation_id,
            transport_error=transport_error,
            evidence_kind=evidence_kind,
            stage_failure=effective_stage_failure,
        )
        return RepairResult(request_id=request.id, attempt=request.attempt, ok=ok, summary=summary, execution_result=execution_result)

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        self._validate_world_verification_contract(request)
        run = await self.instance.run(
            prompt=request.compiled_prompt(),
            model=self._resolve_stage_model(request.metadata, "verify"),
            title="verify",
        )
        artifact, evidence_text, ok, transport_error, evidence_kind, summary, stage_failure = self._materialize_stage_result(
            stage="verification",
            request_id=request.id,
            work_packet_kind=request.work_packet_kind,
            run=run,
        )
        artifacts, bundle, ok, evidence_kind, transport_error, summary, effective_stage_failure, evidence_text, run, selected_artifact = await self._bundle_with_json_handoff(
            stage="verification",
            request=request,
            run=run,
            artifact=artifact,
            evidence_text=evidence_text,
            ok=ok,
            transport_error=transport_error,
            evidence_kind=evidence_kind,
            summary=summary,
            stage_failure=stage_failure,
            work_packet_kind=WorkPacketKind.VERIFY,
        )
        strict_failure = effective_stage_failure if isinstance(effective_stage_failure, OpenHandsRunFailure) and effective_stage_failure.failure_kind == StageFailureKind.EVIDENCE_CONTRACT_MISSING else None
        passed = _verification_passed_from_bundle(text=evidence_text, ok=ok, transport_error=transport_error, bundle=bundle)
        checks_passed = request.checks if passed else []
        checks_failed = [] if passed else list(request.checks)
        missing_evidence = ["usable verification evidence"] if transport_error or not run.text.strip() else []
        if strict_failure is not None:
            missing_evidence.append("structured evidence contract")
        return VerificationResult(
            request_id=request.id,
            passed=passed,
            summary=summary,
            evidence_text=evidence_text,
            artifacts=artifacts,
            structured_evidence=bundle.structured,
            evidence_bundle=bundle,
            primary_evidence_artifact_ids=[bundle.structured_artifact_id] if bundle.structured_artifact_id else [],
            raw_evidence_artifact_id=selected_artifact.id,
            conversation_id=run.conversation_id,
            transport_error=transport_error,
            evidence_kind=evidence_kind,
            stage_failure=effective_stage_failure,
            checks_passed=checks_passed,
            checks_failed=checks_failed,
            missing_evidence=missing_evidence,
            confidence="low" if missing_evidence else "medium",
            verifier_backend="openhands_world_check",
            mode=VerificationMode.WORLD_CHECK,
        )
