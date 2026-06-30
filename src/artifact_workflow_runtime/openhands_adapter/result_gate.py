from __future__ import annotations

from dataclasses import dataclass

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.models import (
    Artifact,
    OpenHandsRunFailure,
    StageFailureKind,
    WorkPacketKind,
)

from .models import OpenHandsRunResult

HTML_MARKERS = ("<!doctype html", "<html", "reactrouter", "window.__reactroutercontext", "let&#x27;s start building")


@dataclass(frozen=True, slots=True)
class StageResultGateDecision:
    usable: bool
    ok: bool
    evidence_kind: str
    transport_error: bool
    evidence_text: str
    artifact_kind: str
    summary: str
    failure: OpenHandsRunFailure | None
    diagnostic_artifact: Artifact | None


class StageResultGate:
    """Validate that an OpenHands run produced usable stage evidence.

    This gate sits below verification/acceptance. It prevents UI HTML, empty
    terminal runs, and missing assistant answers from being converted into
    operational evidence. Verification should verify facts, not discover that the
    producer stage silently failed.
    """

    def __init__(self, artifact_store: ArtifactStore) -> None:
        self.artifact_store = artifact_store

    def evaluate(
        self,
        *,
        stage: str,
        request_id: str,
        work_packet_kind: WorkPacketKind,
        run: OpenHandsRunResult,
    ) -> StageResultGateDecision:
        text = run.text or ""
        stripped = text.strip()
        lowered = stripped.lower()
        evidence_kind = "agent_text"
        transport_error = False
        failure_kind: StageFailureKind | None = None
        retryable = True

        if not stripped:
            evidence_kind = "empty_response"
            failure_kind = StageFailureKind.AGENT_NO_RESULT
            transport_error = False
        elif any(marker in lowered for marker in HTML_MARKERS):
            evidence_kind = "html_transport_error"
            failure_kind = StageFailureKind.HTML_TRANSPORT_ERROR
            transport_error = True
        elif _looks_like_transport_error(lowered, run.status):
            evidence_kind = "transport_error"
            failure_kind = StageFailureKind.TRANSPORT_ERROR
            transport_error = True

        if failure_kind is None:
            return StageResultGateDecision(
                usable=True,
                ok=True,
                evidence_kind=evidence_kind,
                transport_error=False,
                evidence_text=text,
                artifact_kind=f"{stage}_evidence",
                summary=stripped[:400],
                failure=None,
                diagnostic_artifact=None,
            )

        summary = _failure_summary(stage=stage, evidence_kind=evidence_kind, failure_kind=failure_kind, status=run.status)
        diagnostic_text = _diagnostic_text(
            stage=stage,
            request_id=request_id,
            work_packet_kind=work_packet_kind,
            failure_kind=failure_kind,
            evidence_kind=evidence_kind,
            conversation_id=run.conversation_id,
            status=run.status,
            summary=summary,
        )
        diagnostic = self.artifact_store.add_text(
            "openhands_stage_failure",
            diagnostic_text,
            metadata={
                "stage": stage,
                "request_id": request_id,
                "work_packet_kind": work_packet_kind.value,
                "failure_kind": failure_kind.value,
                "evidence_kind": evidence_kind,
                "conversation_id": run.conversation_id,
                "terminal_state": run.status,
                "raw_response_persisted": False,
            },
        )
        failure = OpenHandsRunFailure(
            stage=stage,
            request_id=request_id,
            work_packet_kind=work_packet_kind,
            failure_kind=failure_kind,
            summary=summary,
            retryable=retryable,
            conversation_id=run.conversation_id,
            terminal_state=run.status,
            evidence_kind=evidence_kind,
            diagnostic_artifact_id=diagnostic.id,
        )
        return StageResultGateDecision(
            usable=False,
            ok=False,
            evidence_kind=evidence_kind,
            transport_error=transport_error,
            evidence_text=diagnostic_text,
            artifact_kind="openhands_stage_failure",
            summary=summary,
            failure=failure,
            diagnostic_artifact=diagnostic,
        )


def _looks_like_transport_error(lowered: str, status: str | None) -> bool:
    if lowered.startswith("error:") or lowered.startswith("transport error:"):
        return True
    terminal = (status or "").strip().lower()
    return terminal in {"error", "failed", "cancelled"} and not lowered


def _failure_summary(*, stage: str, evidence_kind: str, failure_kind: StageFailureKind, status: str | None) -> str:
    if failure_kind == StageFailureKind.HTML_TRANSPORT_ERROR:
        return f"OpenHands {stage} returned the web UI HTML/SPA shell instead of agent evidence."
    if failure_kind == StageFailureKind.AGENT_NO_RESULT:
        return f"OpenHands {stage} reached a terminal/no-result state without returning usable agent evidence."
    return f"OpenHands {stage} did not return usable agent evidence ({evidence_kind}; status={status or 'unknown'})."


def _diagnostic_text(
    *,
    stage: str,
    request_id: str,
    work_packet_kind: WorkPacketKind,
    failure_kind: StageFailureKind,
    evidence_kind: str,
    conversation_id: str | None,
    status: str | None,
    summary: str,
) -> str:
    return (
        f"OpenHands stage failure during {stage}.\n"
        f"request_id: {request_id}\n"
        f"work_packet_kind: {work_packet_kind.value}\n"
        f"failure_kind: {failure_kind.value}\n"
        f"evidence_kind: {evidence_kind}\n"
        f"conversation_id: {conversation_id or 'unknown'}\n"
        f"terminal_state: {status or 'unknown'}\n"
        f"summary: {summary}\n"
        "raw_response_persisted: false\n"
        "stage_result_usable: false\n"
    )
