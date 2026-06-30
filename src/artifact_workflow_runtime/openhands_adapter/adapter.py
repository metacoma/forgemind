from __future__ import annotations

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.model_routing import ModelRoutingConfig
from artifact_workflow_runtime.models import (
    ExecutionRequest,
    ExecutionResult,
    ObservationRequest,
    ObservationResult,
    VerificationRequest,
    VerificationResult,
)

from .instance import OpenHandsInstance


HTML_MARKERS = ("<!doctype html", "<html", "reactrouter", "window.__reactroutercontext", "let&#x27;s start building")


def _classify_run_text(text: str) -> tuple[bool, str]:
    stripped = text.strip()
    if not stripped:
        return True, "empty_response"
    lowered = stripped.lower()
    if any(marker in lowered for marker in HTML_MARKERS):
        return True, "html_transport_error"
    return False, "agent_text"


class OpenHandsAdapter:
    def __init__(self, instance: OpenHandsInstance, artifact_store: ArtifactStore, model_routing: ModelRoutingConfig | None = None) -> None:
        self.instance = instance
        self.artifact_store = artifact_store
        self.model_routing = model_routing

    def _resolve_stage_model(self, metadata: dict[str, object] | None, fallback_slot: str) -> str | None:
        metadata = metadata or {}
        explicit = metadata.get("model_override")
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip()
        slot_obj = metadata.get("model_slot")
        slot = str(slot_obj).strip() if isinstance(slot_obj, str) and slot_obj.strip() else fallback_slot
        default_model = getattr(self.instance, "default_model", None)
        return self.model_routing.resolve_openhands(slot, default_model) if self.model_routing else default_model

    async def observe(self, request: ObservationRequest) -> ObservationResult:
        slot = "research" if request.metadata.get("source") == "fresh_external_research" else "observe"
        run = await self.instance.run(
            prompt=request.prompt,
            model=self._resolve_stage_model(request.metadata, slot),
            title=f"observe:{request.task_id}",
        )
        transport_error, evidence_kind = _classify_run_text(run.text)
        artifact = self.artifact_store.add_text(
            "observation_evidence",
            run.text,
            metadata={"conversation_id": run.conversation_id, "request_id": request.id, "evidence_kind": evidence_kind},
        )
        summary = run.text.strip()[:400] if not transport_error else "OpenHands did not return usable observation evidence."
        return ObservationResult(
            request_id=request.id,
            ok=bool(run.text.strip()) and not transport_error,
            summary=summary,
            evidence_text=run.text,
            artifacts=[artifact],
            conversation_id=run.conversation_id,
            transport_error=transport_error,
            evidence_kind=evidence_kind,
        )

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        run = await self.instance.run(
            prompt=request.prompt,
            model=self._resolve_stage_model(request.metadata, "execute"),
            title=f"execute:{request.task_id}",
        )
        transport_error, evidence_kind = _classify_run_text(run.text)
        artifact = self.artifact_store.add_text(
            "execution_evidence",
            run.text,
            metadata={"conversation_id": run.conversation_id, "request_id": request.id, "evidence_kind": evidence_kind},
        )
        summary = run.text.strip()[:400] if not transport_error else "OpenHands did not return usable execution evidence."
        return ExecutionResult(
            request_id=request.id,
            ok=bool(run.text.strip()) and not transport_error,
            summary=summary,
            evidence_text=run.text,
            artifacts=[artifact],
            conversation_id=run.conversation_id,
            transport_error=transport_error,
            evidence_kind=evidence_kind,
        )

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        run = await self.instance.run(
            prompt=request.prompt,
            model=self._resolve_stage_model(request.metadata, "verify"),
            title="verify",
        )
        transport_error, evidence_kind = _classify_run_text(run.text)
        text_lower = run.text.lower()
        passed = ("pass" in text_lower or "ok" in text_lower or "success" in text_lower) and not transport_error
        artifact = self.artifact_store.add_text(
            "verification_evidence",
            run.text,
            metadata={"conversation_id": run.conversation_id, "request_id": request.id, "evidence_kind": evidence_kind},
        )
        summary = run.text.strip()[:400] if not transport_error else "OpenHands did not return usable verification evidence."
        checks_passed = request.checks if passed else []
        checks_failed = [] if passed else list(request.checks)
        missing_evidence = ["usable verification evidence"] if transport_error or not run.text.strip() else []
        return VerificationResult(
            request_id=request.id,
            passed=passed,
            summary=summary,
            evidence_text=run.text,
            artifacts=[artifact],
            conversation_id=run.conversation_id,
            checks_passed=checks_passed,
            checks_failed=checks_failed,
            missing_evidence=missing_evidence,
            confidence="low" if missing_evidence else "medium",
            verifier_backend="openhands",
        )
