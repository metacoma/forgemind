from __future__ import annotations

import os
import subprocess
from typing import Any

from artifact_workflow_runtime.artifacts import ArtifactStore
from artifact_workflow_runtime.evidence import EvidenceExtractor
from artifact_workflow_runtime.models import BackendKind, EvidenceBundle, PublishRequest, PublishResult, StructuredEvidence


class DeterministicPublisher:
    def __init__(self, artifact_store: ArtifactStore, scripted_source: Any | None = None) -> None:
        self.artifact_store = artifact_store
        self.scripted_source = scripted_source
        self.evidence_extractor = EvidenceExtractor()

    async def publish(self, request: PublishRequest) -> PublishResult:
        text = self._scripted_publish(request)
        if text is None:
            text = self._local_publish(request)
        raw = self.artifact_store.add_text("publish_output", text, metadata={"request_id": request.id, "backend": "deterministic_publisher"})
        structured = self.evidence_extractor.from_agent_output(text, artifact_id=raw.id, strict=False)
        bundle = EvidenceBundle(
            source_backend=BackendKind.DIRECT_LLM,
            work_packet_kind=request.work_packet_kind,
            ok=True,
            summary=text.splitlines()[0] if text.strip() else "Deterministic publish completed.",
            artifact_ids=[raw.id],
            structured=structured,
            evidence_kind="deterministic_text",
            raw_text_artifact_id=raw.id,
            blockers=[item.summary for item in structured.blockers],
        )
        bundle_artifact = self.artifact_store.add_json("structured_evidence_bundle", bundle.model_dump(mode="json"), metadata={"request_id": request.id, "backend": "deterministic_publisher"})
        bundle.artifact_ids.append(bundle_artifact.id)
        bundle.structured_artifact_id = bundle_artifact.id
        return PublishResult(
            request_id=request.id,
            ok=not bool(structured.blockers),
            summary=bundle.summary,
            evidence_text=text,
            artifacts=[raw, bundle_artifact],
            structured_evidence=structured,
            evidence_bundle=bundle,
            primary_evidence_artifact_ids=[bundle_artifact.id],
            raw_evidence_artifact_id=raw.id,
            conversation_id=None,
            transport_error=False,
            evidence_kind="deterministic_text",
            stage_failure=None,
        )

    def _scripted_publish(self, request: PublishRequest) -> str | None:
        source = self.scripted_source
        if source is None:
            return None
        calls = getattr(source, "calls", None)
        if isinstance(calls, dict):
            calls.setdefault("publish", []).append(request)
        scripts = getattr(source, "scripts", None)
        if isinstance(scripts, dict) and scripts.get("publish"):
            return scripts["publish"].pop(0)
        return None

    def _local_publish(self, request: PublishRequest) -> str:
        lines = ["Deterministic publish backend started."]
        cwd = os.getcwd()
        if request.require_commit:
            lines.append(self._run("git status --short", cwd))
        if request.require_push:
            lines.append("Push requested but no default remote push is performed automatically by deterministic publisher.")
        return "\n".join(lines)

    @staticmethod
    def _run(command: str, cwd: str) -> str:
        completed = subprocess.run(command, shell=True, cwd=cwd, text=True, capture_output=True, timeout=120)
        output = (completed.stdout or "") + (("\n" + completed.stderr) if completed.stderr else "")
        return f"$ {command}\n{output}".strip()
