from __future__ import annotations

from typing import Mapping

from artifact_workflow_runtime.models import Artifact, ContextPacket, ContextSection, Task


class ContextBuilder:
    """Build the only text bridge from world artifacts to Direct LLM reasoning."""

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
            body = self._clip(body)
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
