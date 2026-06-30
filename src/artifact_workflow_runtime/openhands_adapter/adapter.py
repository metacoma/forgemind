from __future__ import annotations

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.evidence import EvidenceExtractor, render_structured_evidence_summary
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
    VerificationMode,
    VerificationRequest,
    VerificationResult,
    StructuredEvidence,
    WorkPacketKind,
)

from .instance import OpenHandsInstance


HTML_MARKERS = ("<!doctype html", "<html", "reactrouter", "window.__reactroutercontext", "let&#x27;s start building")
_MUTATING_OBSERVE_ACTIONS = {"edit_files", "write_files", "commit", "push", "git push", "apply_cluster_changes", "change_host_config", "run_write_commands"}
_DIRECT_VERIFY_FORBIDDEN_ACTIONS = {"filesystem", "shell", "git", "hosts", "kubernetes", "network_runtime_state"}
_READ_ONLY_CAPABILITY_VALUES = {"document_read", "repo_read", "shell_read", "git_read", "k8s_read", "network_diagnostics"}
_NON_PUBLISH_GIT_GUARDS = {"commit", "push", "git push", "git push --force", "git tag", "git merge", "git rebase", "create_pr", "open_pull_request", "publish", "release"}
_DESTRUCTIVE_PUBLISH_GUARDS = {"git push --force", "git tag", "git merge", "git rebase", "release", "fix_ci_after_publish", "reimplement_feature", "apply_feature_changes", "edit_source_files", "repair"}


def _classify_run_text(text: str) -> tuple[bool, str]:
    stripped = text.strip()
    if not stripped:
        return True, "empty_response"
    lowered = stripped.lower()
    if any(marker in lowered for marker in HTML_MARKERS):
        return True, "html_transport_error"
    return False, "agent_text"


def _stage_transport_error_text(*, stage: str, evidence_kind: str, conversation_id: str | None, status: str | None) -> str:
    conversation = conversation_id or "unknown"
    last_status = status or "unknown"
    if evidence_kind == "html_transport_error":
        cause = (
            "OpenHands returned the web UI HTML/SPA shell instead of agent result text. "
            "This usually means the runtime used a UI route or stale/wrong agent-server URL/session key "
            "instead of the agent conversation/event API. The HTML body was intentionally suppressed and "
            "must not be treated as verification evidence."
        )
    elif evidence_kind == "empty_response":
        cause = "OpenHands returned no usable agent result text."
    else:
        cause = f"OpenHands returned unusable agent result text ({evidence_kind})."
    return (
        f"OpenHands transport error during {stage}.\n"
        f"evidence_kind: {evidence_kind}\n"
        f"conversation_id: {conversation}\n"
        f"last_status: {last_status}\n"
        f"cause: {cause}\n"
        "raw_response_persisted: false\n"
    )


def _stage_evidence_payload(*, stage: str, run_text: str, transport_error: bool, evidence_kind: str, conversation_id: str | None, status: str | None) -> tuple[str, str, str]:
    """Return (artifact_kind, evidence_text, summary) for an OpenHands stage.

    UI HTML/SPA fallbacks are transport failures, not operational evidence. Keep
    them out of evidence artifacts so later context/verification cannot treat a
    React shell as proof that a check ran.
    """
    if not transport_error:
        return f"{stage}_evidence", run_text, run_text.strip()[:400]
    text = _stage_transport_error_text(stage=stage, evidence_kind=evidence_kind, conversation_id=conversation_id, status=status)
    return "openhands_transport_error", text, f"OpenHands did not return usable {stage} evidence."


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


class OpenHandsAdapter:
    def __init__(self, instance: OpenHandsInstance, artifact_store: ArtifactStore, model_routing: ModelRoutingConfig | None = None) -> None:
        self.instance = instance
        self.artifact_store = artifact_store
        self.model_routing = model_routing
        self.evidence_extractor = EvidenceExtractor()

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
        values = [*getattr(request, "forbidden_actions", []), *getattr(request, "forbidden_inputs", [])]
        return {str(item).strip().lower() for item in values}

    @staticmethod
    def _allowed_set(request: object) -> set[str]:
        values = [*getattr(request, "allowed_actions", []), *getattr(request, "allowed_inputs", [])]
        return {str(item).strip().lower() for item in values}

    @staticmethod
    def _require_forbidden_actions(request: object, required: set[str], label: str) -> None:
        forbidden = OpenHandsAdapter._forbidden_set(request)
        missing = required - forbidden
        if missing:
            raise ValueError(f"{label} packets must explicitly forbid: {sorted(missing)}")

    @staticmethod
    def _validate_compiled_prompt_contains_contract(request: object, *, label: str) -> None:
        prompt = request.compiled_prompt()
        required_markers = (
            "## Non-negotiable control-plane boundary",
            "## Allowed actions",
            "## Forbidden actions",
            "## Stop conditions",
            "## Required outputs",
            "Do not choose the next workflow step",
            "if an action is not explicitly allowed, treat it as forbidden",
        )
        missing = [marker for marker in required_markers if marker not in prompt]
        if missing:
            raise ValueError(f"{label} compiled prompt is missing stage-contract sections: {missing}")

    @staticmethod
    def _validate_observation_contract(request: ObservationRequest) -> None:
        if request.work_packet_kind not in {WorkPacketKind.OBSERVE, WorkPacketKind.RESEARCH}:
            raise ValueError(f"OpenHands observe() only accepts observe/research packets, got {request.work_packet_kind}")
        allowed = OpenHandsAdapter._allowed_set(request)
        if allowed & _MUTATING_OBSERVE_ACTIONS:
            raise ValueError(f"Observation packets cannot allow mutating actions: {sorted(allowed & _MUTATING_OBSERVE_ACTIONS)}")
        mutating_capabilities = {cap.value for cap in request.capabilities if cap.value not in _READ_ONLY_CAPABILITY_VALUES}
        if mutating_capabilities:
            raise ValueError(f"Observation packets cannot carry mutating capabilities: {sorted(mutating_capabilities)}")
        OpenHandsAdapter._require_forbidden_actions(request, {"edit_files", "write_files", "commit", "push", "git push", "create_pr", "open_pull_request", "publish"}, "Observation")
        if request.evidence_requirements.require_structured is not True:
            raise ValueError("Observation packets must require structured evidence as the operational output")
        OpenHandsAdapter._validate_compiled_prompt_contains_contract(request, label="Observation")

    @staticmethod
    def _validate_execution_contract(request: ExecutionRequest) -> None:
        if request.work_packet_kind != WorkPacketKind.EXECUTE:
            raise ValueError(f"OpenHands execute() only accepts execute packets; publish packets must use publish() (not execute/publish packets), got {request.work_packet_kind}")
        if not request.expected_outputs:
            raise ValueError("Execution packets must declare expected_outputs")
        forbidden = OpenHandsAdapter._forbidden_set(request)
        if "change_workflow_decision" not in forbidden:
            raise ValueError("Execution packets must forbid changing workflow decisions")
        OpenHandsAdapter._require_forbidden_actions(request, _NON_PUBLISH_GIT_GUARDS, "Execution")
        if request.evidence_requirements.require_structured is not True:
            raise ValueError("Execution packets must require structured evidence as the operational output")
        OpenHandsAdapter._validate_compiled_prompt_contains_contract(request, label="Execution")


    @staticmethod
    def _validate_publish_contract(request: PublishRequest) -> None:
        if request.work_packet_kind != WorkPacketKind.PUBLISH:
            raise ValueError(f"OpenHands publish() only accepts publish packets, got {request.work_packet_kind}")
        if not request.expected_outputs:
            raise ValueError("Publish packets must declare expected_outputs")
        forbidden = OpenHandsAdapter._forbidden_set(request)
        if "change_workflow_decision" not in forbidden:
            raise ValueError("Publish packets must forbid changing workflow decisions")
        if "reimplement_feature" not in forbidden or "expand_task_scope" not in forbidden:
            raise ValueError("Publish packets must forbid reimplementation and scope expansion")
        OpenHandsAdapter._require_forbidden_actions(request, _DESTRUCTIVE_PUBLISH_GUARDS, "Publish")
        if request.evidence_requirements.require_structured is not True:
            raise ValueError("Publish packets must require structured evidence as the operational output")
        OpenHandsAdapter._validate_compiled_prompt_contains_contract(request, label="Publish")


    @staticmethod
    def _validate_repair_contract(request: RepairRequest) -> None:
        if request.work_packet_kind != WorkPacketKind.REPAIR:
            raise ValueError(f"OpenHands repair() only accepts repair packets, got {request.work_packet_kind}")
        OpenHandsAdapter._require_forbidden_actions(request, _NON_PUBLISH_GIT_GUARDS, "Repair")
        if request.attempt < 1 or request.attempt > request.max_attempts:
            raise ValueError("Repair packet attempt is outside the configured repair budget")
        if request.evidence_requirements.require_structured is not True:
            raise ValueError("Repair packets must require structured evidence as the operational output")
        OpenHandsAdapter._validate_compiled_prompt_contains_contract(request, label="Repair")

    @staticmethod
    def _validate_world_verification_contract(request: VerificationRequest) -> None:
        if request.work_packet_kind != WorkPacketKind.VERIFY:
            raise ValueError(f"OpenHands verify() only accepts verify packets, got {request.work_packet_kind}")
        if request.backend != BackendKind.OPENHANDS or request.mode != VerificationMode.WORLD_CHECK:
            raise ValueError("OpenHands verify() requires backend=openhands and mode=world_check")
        forbidden = {item.strip().lower() for item in request.forbidden_inputs}
        # World checks may inspect filesystem/shell/git, so they must not inherit the Direct LLM forbidden-input contract.
        if forbidden & _DIRECT_VERIFY_FORBIDDEN_ACTIONS == _DIRECT_VERIFY_FORBIDDEN_ACTIONS:
            raise ValueError("World verification packets must declare world-check inputs/actions explicitly, not Direct LLM forbidden inputs")
        OpenHandsAdapter._require_forbidden_actions(request, {"edit_files", "write_files", "fix_code", "repair", "commit", "push", "git push", "create_pr", "open_pull_request", "publish"}, "World verification")
        if request.evidence_requirements.require_structured is not True:
            raise ValueError("World verification packets must require structured evidence as the operational output")
        OpenHandsAdapter._validate_compiled_prompt_contains_contract(request, label="World verification")

    def _evidence_bundle(self, *, text: str, raw_artifact_id: str, request_id: str, ok: bool, summary: str, evidence_kind: str, work_packet_kind: WorkPacketKind, changed_default: bool = False) -> tuple[EvidenceBundle, object]:
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
        self._validate_observation_contract(request)
        slot = "research" if request.work_packet_kind == WorkPacketKind.RESEARCH or request.metadata.get("source") == "fresh_external_research" else "observe"
        run = await self.instance.run(
            prompt=request.compiled_prompt(),
            model=self._resolve_stage_model(request.metadata, slot),
            title=f"observe:{request.task_id}",
        )
        transport_error, evidence_kind = _classify_run_text(run.text)
        ok = bool(run.text.strip()) and not transport_error
        artifact_kind, evidence_text, summary = _stage_evidence_payload(
            stage="observation",
            run_text=run.text,
            transport_error=transport_error,
            evidence_kind=evidence_kind,
            conversation_id=run.conversation_id,
            status=run.status,
        )
        artifact = self.artifact_store.add_text(
            artifact_kind,
            evidence_text,
            metadata={"conversation_id": run.conversation_id, "request_id": request.id, "evidence_kind": evidence_kind, "raw_response_persisted": not transport_error},
        )
        bundle, bundle_artifact = self._evidence_bundle(
            text=evidence_text,
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
            evidence_text=evidence_text,
            artifacts=[artifact, bundle_artifact],
            structured_evidence=bundle.structured,
            evidence_bundle=bundle,
            primary_evidence_artifact_ids=[bundle_artifact.id],
            raw_evidence_artifact_id=artifact.id,
            conversation_id=run.conversation_id,
            transport_error=transport_error,
            evidence_kind=evidence_kind,
        )

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self._validate_execution_contract(request)
        slot = "execute"
        run = await self.instance.run(
            prompt=request.compiled_prompt(),
            model=self._resolve_stage_model(request.metadata, slot),
            title=f"execute:{request.task_id}",
        )
        transport_error, evidence_kind = _classify_run_text(run.text)
        ok = bool(run.text.strip()) and not transport_error
        artifact_kind, evidence_text, summary = _stage_evidence_payload(
            stage="execution",
            run_text=run.text,
            transport_error=transport_error,
            evidence_kind=evidence_kind,
            conversation_id=run.conversation_id,
            status=run.status,
        )
        artifact = self.artifact_store.add_text(
            artifact_kind,
            evidence_text,
            metadata={"conversation_id": run.conversation_id, "request_id": request.id, "evidence_kind": evidence_kind, "raw_response_persisted": not transport_error},
        )
        bundle, bundle_artifact = self._evidence_bundle(
            text=evidence_text,
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
            evidence_text=evidence_text,
            artifacts=[artifact, bundle_artifact],
            structured_evidence=bundle.structured,
            evidence_bundle=bundle,
            primary_evidence_artifact_ids=[bundle_artifact.id],
            raw_evidence_artifact_id=artifact.id,
            conversation_id=run.conversation_id,
            transport_error=transport_error,
            evidence_kind=evidence_kind,
        )

    async def publish(self, request: PublishRequest) -> PublishResult:
        self._validate_publish_contract(request)
        run = await self.instance.run(
            prompt=request.compiled_prompt(),
            model=self._resolve_stage_model(request.metadata, "publish"),
            title=f"publish:{request.task_id}",
        )
        transport_error, evidence_kind = _classify_run_text(run.text)
        ok = bool(run.text.strip()) and not transport_error
        artifact_kind, evidence_text, summary = _stage_evidence_payload(
            stage="publish",
            run_text=run.text,
            transport_error=transport_error,
            evidence_kind=evidence_kind,
            conversation_id=run.conversation_id,
            status=run.status,
        )
        artifact = self.artifact_store.add_text(
            artifact_kind,
            evidence_text,
            metadata={"conversation_id": run.conversation_id, "request_id": request.id, "evidence_kind": evidence_kind, "raw_response_persisted": not transport_error},
        )
        bundle, bundle_artifact = self._evidence_bundle(
            text=evidence_text,
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
            evidence_text=evidence_text,
            artifacts=[artifact, bundle_artifact],
            structured_evidence=bundle.structured,
            evidence_bundle=bundle,
            primary_evidence_artifact_ids=[bundle_artifact.id],
            raw_evidence_artifact_id=artifact.id,
            conversation_id=run.conversation_id,
            transport_error=transport_error,
            evidence_kind=evidence_kind,
        )


    async def repair(self, request: RepairRequest) -> RepairResult:
        self._validate_repair_contract(request)
        run = await self.instance.run(
            prompt=request.compiled_prompt(),
            model=self._resolve_stage_model(request.metadata, "execute"),
            title=f"repair:{request.task_id}:attempt-{request.attempt}",
        )
        transport_error, evidence_kind = _classify_run_text(run.text)
        ok = bool(run.text.strip()) and not transport_error
        artifact_kind, evidence_text, summary = _stage_evidence_payload(
            stage="repair",
            run_text=run.text,
            transport_error=transport_error,
            evidence_kind=evidence_kind,
            conversation_id=run.conversation_id,
            status=run.status,
        )
        artifact = self.artifact_store.add_text(
            artifact_kind,
            evidence_text,
            metadata={"conversation_id": run.conversation_id, "request_id": request.id, "evidence_kind": evidence_kind, "attempt": request.attempt, "raw_response_persisted": not transport_error},
        )
        bundle, bundle_artifact = self._evidence_bundle(
            text=evidence_text,
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
            evidence_text=evidence_text,
            artifacts=[artifact, bundle_artifact],
            structured_evidence=bundle.structured,
            evidence_bundle=bundle,
            primary_evidence_artifact_ids=[bundle_artifact.id],
            raw_evidence_artifact_id=artifact.id,
            conversation_id=run.conversation_id,
            transport_error=transport_error,
            evidence_kind=evidence_kind,
        )
        return RepairResult(request_id=request.id, attempt=request.attempt, ok=ok, summary=summary, execution_result=execution_result)

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        self._validate_world_verification_contract(request)
        run = await self.instance.run(
            prompt=request.compiled_prompt(),
            model=self._resolve_stage_model(request.metadata, "verify"),
            title="verify",
        )
        transport_error, evidence_kind = _classify_run_text(run.text)
        ok = bool(run.text.strip()) and not transport_error
        artifact_kind, evidence_text, summary = _stage_evidence_payload(
            stage="verification",
            run_text=run.text,
            transport_error=transport_error,
            evidence_kind=evidence_kind,
            conversation_id=run.conversation_id,
            status=run.status,
        )
        artifact = self.artifact_store.add_text(
            artifact_kind,
            evidence_text,
            metadata={"conversation_id": run.conversation_id, "request_id": request.id, "evidence_kind": evidence_kind, "raw_response_persisted": not transport_error},
        )
        bundle, bundle_artifact = self._evidence_bundle(
            text=evidence_text,
            raw_artifact_id=artifact.id,
            request_id=request.id,
            ok=ok,
            summary=summary,
            evidence_kind=evidence_kind,
            work_packet_kind=WorkPacketKind.VERIFY,
        )
        passed = _verification_passed_from_bundle(text=evidence_text, ok=ok, transport_error=transport_error, bundle=bundle)
        checks_passed = request.checks if passed else []
        checks_failed = [] if passed else list(request.checks)
        missing_evidence = ["usable verification evidence"] if transport_error or not run.text.strip() else []
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
            conversation_id=run.conversation_id,
            transport_error=transport_error,
            evidence_kind=evidence_kind,
            checks_passed=checks_passed,
            checks_failed=checks_failed,
            missing_evidence=missing_evidence,
            confidence="low" if missing_evidence else "medium",
            verifier_backend="openhands_world_check",
            mode=VerificationMode.WORLD_CHECK,
        )
