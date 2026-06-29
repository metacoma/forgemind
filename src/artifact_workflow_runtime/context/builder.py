from __future__ import annotations

from artifact_workflow_runtime.models import Artifact, ContextPacket, ContextSection, Task


class ContextBuilder:
    def build(self, task: Task, artifacts: list[Artifact], *, artifact_texts: dict[str, str] | None = None) -> ContextPacket:
        sections: list[ContextSection] = [ContextSection(title="Task", body=task.description)]
        artifact_ids: list[str] = []
        texts = artifact_texts or {}
        for artifact in artifacts:
            artifact_ids.append(artifact.id)
            body = texts.get(artifact.id) or artifact.text_preview or ""
            sections.append(ContextSection(title=f"Artifact: {artifact.kind}", body=body, artifact_id=artifact.id))
        text_parts = [f"## {section.title}\n{section.body}".strip() for section in sections]
        return ContextPacket(task_id=task.id, artifact_ids=artifact_ids, sections=sections, text="\n\n".join(text_parts))
