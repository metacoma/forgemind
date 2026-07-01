
from __future__ import annotations

import json
import re
from typing import Any, Mapping

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.evidence import EvidenceContractError, EvidenceExtractor
from artifact_workflow_runtime.models import OpenHandsMachineHandoff, RuntimeModel, StructuredEvidence, StructuredResponseContract


_FENCED_JSON_RE = re.compile(r"```(?:json|JSON)?\s*(?P<body>.*?)```", re.DOTALL)
_EVIDENCE_HINT_KEYS = {
    "structured_evidence",
    "commands_run",
    "files_changed",
    "files_observed",
    "extracted_facts",
    "facts",
    "diffs",
    "tests",
    "checks",
    "blockers",
    "unknowns",
    "missing_evidence",
    "mutation_summary",
    "postcheck_summary",
    "repair_summary",
}


class OpenHandsJsonCandidate(RuntimeModel):
    source: str
    payload: Any
    score: int = 0


class NormalizedOpenHandsResponse(RuntimeModel):
    raw_artifact_id: str
    extracted_payload_artifact_id: str | None = None
    normalized_payload_artifact_id: str | None = None
    normalized_payload: dict[str, Any] | None = None
    contract_status: str
    extraction_mode: str = "none"
    fallback_extraction_used: bool = False
    validation_result: dict[str, Any] = {}
    structured_evidence: StructuredEvidence | None = None


class OpenHandsResponseNormalizer:
    """Structured-first, container-tolerant normalization layer.

    This sits between raw OpenHands text and the control plane. It extracts the
    best JSON payload, validates/normalizes it, and persists provenance artifacts
    before the adapter builds typed runtime results.
    """

    def __init__(self, artifact_store: ArtifactStore, extractor: EvidenceExtractor) -> None:
        self.artifact_store = artifact_store
        self.extractor = extractor

    def normalize(
        self,
        *,
        stage: str,
        request_id: str,
        raw_text: str,
        raw_artifact_id: str,
        response_contract: StructuredResponseContract | None,
        strict: bool,
        changed_default: bool = False,
    ) -> NormalizedOpenHandsResponse:
        selected = self._select_best_candidate(raw_text=raw_text, response_contract=response_contract)
        if selected is None:
            if strict:
                return NormalizedOpenHandsResponse(
                    raw_artifact_id=raw_artifact_id,
                    contract_status="missing_structured_json",
                    extraction_mode="none",
                    fallback_extraction_used=False,
                    validation_result={
                        "stage": stage,
                        "request_id": request_id,
                        "reason": "No structured JSON candidate satisfied the OpenHands response contract.",
                    },
                )
            structured = self.extractor.from_text(raw_text, artifact_id=raw_artifact_id, changed_default=changed_default)
            normalized_payload = self._canonical_payload(summary="Raw text fallback", payload=None, structured=structured)
            normalized_artifact = self.artifact_store.add_json(
                "openhands_normalized_response",
                normalized_payload,
                metadata={
                    "request_id": request_id,
                    "stage": stage,
                    "contract_status": "text_fallback",
                    "extraction_mode": "none",
                },
            )
            return NormalizedOpenHandsResponse(
                raw_artifact_id=raw_artifact_id,
                normalized_payload_artifact_id=normalized_artifact.id,
                normalized_payload=normalized_payload,
                contract_status="text_fallback",
                extraction_mode="none",
                validation_result={"stage": stage, "request_id": request_id, "reason": "Structured JSON not found; text fallback used."},
                structured_evidence=structured,
            )

        extracted_artifact = self.artifact_store.add_json(
            "openhands_extracted_payload",
            selected.payload,
            metadata={
                "request_id": request_id,
                "stage": stage,
                "extraction_mode": selected.source,
                "fallback_extraction_used": selected.source != "raw_json",
            },
        )

        try:
            structured = self.extractor.from_payload(
                selected.payload,
                artifact_id=raw_artifact_id,
                changed_default=changed_default,
                strict=True,
            )
        except EvidenceContractError as exc:
            return NormalizedOpenHandsResponse(
                raw_artifact_id=raw_artifact_id,
                extracted_payload_artifact_id=extracted_artifact.id,
                contract_status="invalid_structured_payload",
                extraction_mode=selected.source,
                fallback_extraction_used=selected.source != "raw_json",
                validation_result={
                    "stage": stage,
                    "request_id": request_id,
                    "reason": str(exc),
                },
            )

        normalized_payload = self._canonical_payload(
            summary=self._payload_summary(selected.payload),
            payload=selected.payload,
            structured=structured,
        )
        normalized_artifact = self.artifact_store.add_json(
            "openhands_normalized_response",
            normalized_payload,
            metadata={
                "request_id": request_id,
                "stage": stage,
                "contract_status": "validated",
                "extraction_mode": selected.source,
                "fallback_extraction_used": selected.source != "raw_json",
            },
        )
        return NormalizedOpenHandsResponse(
            raw_artifact_id=raw_artifact_id,
            extracted_payload_artifact_id=extracted_artifact.id,
            normalized_payload_artifact_id=normalized_artifact.id,
            normalized_payload=normalized_payload,
            contract_status="validated",
            extraction_mode=selected.source,
            fallback_extraction_used=selected.source != "raw_json",
            validation_result={
                "stage": stage,
                "request_id": request_id,
                "selected_source": selected.source,
                "score": selected.score,
            },
            structured_evidence=structured,
        )

    def _select_best_candidate(self, *, raw_text: str, response_contract: StructuredResponseContract | None) -> OpenHandsJsonCandidate | None:
        candidates = self._json_candidates(raw_text)
        if not candidates:
            return None
        best: OpenHandsJsonCandidate | None = None
        for candidate in candidates:
            if not isinstance(candidate.payload, Mapping):
                continue
            candidate.score = self._score_candidate(candidate.payload, response_contract=response_contract)
            if best is None or candidate.score > best.score:
                best = candidate
        if best is None:
            return None
        if best.score <= 0:
            return None
        return best

    def _json_candidates(self, text: str) -> list[OpenHandsJsonCandidate]:
        stripped = text.strip()
        candidates: list[OpenHandsJsonCandidate] = []
        if not stripped:
            return candidates
        try:
            candidates.append(OpenHandsJsonCandidate(source="raw_json", payload=json.loads(stripped)))
        except json.JSONDecodeError:
            pass
        for match in _FENCED_JSON_RE.finditer(text):
            body = match.group("body").strip()
            if not body:
                continue
            try:
                candidates.append(OpenHandsJsonCandidate(source="fenced_json", payload=json.loads(body)))
            except json.JSONDecodeError:
                continue
        decoder = json.JSONDecoder()
        for idx, ch in enumerate(text):
            if ch not in "[{":
                continue
            try:
                payload, _ = decoder.raw_decode(text[idx:])
            except json.JSONDecodeError:
                continue
            candidates.append(OpenHandsJsonCandidate(source="scanned_json", payload=payload))
        deduped: list[OpenHandsJsonCandidate] = []
        seen: set[str] = set()
        for candidate in candidates:
            try:
                key = json.dumps(candidate.payload, ensure_ascii=False, sort_keys=True)
            except TypeError:
                key = repr(candidate.payload)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(candidate)
        return deduped

    def _score_candidate(self, payload: Mapping[str, Any], *, response_contract: StructuredResponseContract | None) -> int:
        score = 0
        keys = set(payload.keys())
        if "structured_evidence" in keys:
            score += 100
        score += len(keys & _EVIDENCE_HINT_KEYS) * 10
        if response_contract is not None:
            required = {field.name for field in response_contract.required_fields if field.required}
            score += len(required & keys) * 20
            if required and required <= keys:
                score += 50
        if any(key in keys for key in ("summary", "blockers", "missing_evidence", "unknowns")):
            score += 10
        return score

    @staticmethod
    def _payload_summary(payload: Mapping[str, Any]) -> str:
        summary = payload.get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()
        return "OpenHands structured response"

    @staticmethod
    def _canonical_payload(*, summary: str, payload: Mapping[str, Any] | None, structured: StructuredEvidence) -> dict[str, Any]:
        if isinstance(payload, Mapping):
            try:
                handoff = OpenHandsMachineHandoff.model_validate(payload)
                return handoff.model_dump(mode="json")
            except Exception:
                pass
        return {
            "summary": summary,
            "structured_evidence": structured.model_dump(mode="json"),
            "blockers": [item.model_dump(mode="json") for item in structured.blockers],
            "missing_evidence": [item.summary for item in structured.blockers if "Missing evidence:" in item.summary],
            "unknowns": [],
        }
