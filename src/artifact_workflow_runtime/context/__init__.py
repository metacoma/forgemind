from __future__ import annotations

import json
from typing import Mapping

from artifact_workflow_runtime.evidence import render_structured_evidence_summary
from artifact_workflow_runtime.models import Artifact, ContextPacket, ContextSection, EvidenceBundle, StructuredEvidence, Task


class ContextBuilder:
    """Build the only text bridge from world artifacts to Direct LLM reasoning.

    Structured evidence artifacts are rendered as compact typed evidence summaries
    first. Raw text remains available only as clipped supplement.
    """

    def __init__(self, *, max_chars_per_artifact: int = 8000) -> None:
        self.max_chars_per_artifact = max_chars_per_artifact

    def build(
        self,
        task: Task,
        artifacts: list[Artifact],
        *,
        artifact_texts: Mapping[str, str] | None = None,
    ) -> ContextPacket:
        artifact_texts = artifact_texts or {}
        sections: list[ContextSection] = [
            ContextSection(title="Task", body=task.description, artifact_id=None),
        ]
        artifact_ids: list[str] = []
        for artifact in artifacts:
            if artifact.id in artifact_ids:
                continue
            artifact_ids.append(artifact.id)
            body = artifact_texts.get(artifact.id)
            if body is None:
                body = artifact.text_preview or ""
            body = self._render_body(artifact, body)
            metadata_lines = []
            if artifact.metadata:
                metadata_lines = [f"{key}: {value}" for key, value in sorted(artifact.metadata.items())]
            header = f"kind={artifact.kind}\nmedia_type={artifact.media_type}\npath={artifact.path}"
            if metadata_lines:
                header += "\n" + "\n".join(metadata_lines)
            sections.append(
                ContextSection(
                    title=f"Artifact {artifact.kind} ({artifact.id})",
                    body=f"{header}\n\n{body}",
                    artifact_id=artifact.id,
                )
            )
        text = self._render(sections)
        return ContextPacket(task_id=task.id, artifact_ids=artifact_ids, sections=sections, text=text)

    def _render_body(self, artifact: Artifact, body: str) -> str:
        if artifact.kind == "structured_evidence_bundle":
            rendered = self._render_structured_evidence(body)
            if rendered:
                return self._clip(rendered)
        return self._clip(body)

    @staticmethod
    def _render_structured_evidence(body: str) -> str | None:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return None
        try:
            if isinstance(payload, dict) and "structured" in payload:
                bundle = EvidenceBundle.model_validate(payload)
                return "## Structured evidence bundle\n" + bundle.operational_summary() + "\n\n" + render_structured_evidence_summary(bundle.structured)
            if isinstance(payload, dict):
                evidence = StructuredEvidence.model_validate(payload.get("structured_evidence", payload))
                return "## Structured evidence\n" + render_structured_evidence_summary(evidence)
        except Exception:
            return None
        return None

    def _clip(self, text: str) -> str:
        if len(text) <= self.max_chars_per_artifact:
            return text
        return text[: self.max_chars_per_artifact] + "\n...[artifact excerpt truncated]"

    @staticmethod
    def _render(sections: list[ContextSection]) -> str:
        parts: list[str] = []
        for section in sections:
            parts.append(f"## {section.title}\n{section.body.strip()}".rstrip())
        return "\n\n".join(parts).strip()
