from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from artifact_workflow_runtime.models import Artifact


class ArtifactStore:
    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root_dir / "artifact_index.json"
        self._index: dict[str, dict[str, Any]] = {}
        if self.index_path.exists():
            self._index = json.loads(self.index_path.read_text(encoding="utf-8"))

    def _save_index(self) -> None:
        self.index_path.write_text(json.dumps(self._index, ensure_ascii=False, indent=2), encoding="utf-8")

    def add_text(self, kind: str, text: str, *, media_type: str = "text/plain", metadata: dict[str, Any] | None = None) -> Artifact:
        artifact = Artifact(
            kind=kind,
            path=str(self.root_dir / f"{kind}_{len(self._index)+1}.txt"),
            media_type=media_type,
            text_preview=text[:500],
            metadata=metadata or {},
        )
        Path(artifact.path).write_text(text, encoding="utf-8")
        self._index[artifact.id] = artifact.model_dump(mode="json")
        self._save_index()
        return artifact

    def add_json(self, kind: str, payload: dict[str, Any], *, metadata: dict[str, Any] | None = None) -> Artifact:
        artifact = Artifact(
            kind=kind,
            path=str(self.root_dir / f"{kind}_{len(self._index)+1}.json"),
            media_type="application/json",
            text_preview=json.dumps(payload, ensure_ascii=False)[:500],
            metadata=metadata or {},
        )
        Path(artifact.path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._index[artifact.id] = artifact.model_dump(mode="json")
        self._save_index()
        return artifact

    def get(self, artifact_id: str) -> Artifact:
        return Artifact.model_validate(self._index[artifact_id])

    def read_text(self, artifact_id: str) -> str:
        artifact = self.get(artifact_id)
        return Path(artifact.path).read_text(encoding="utf-8")

    def list_ids(self) -> list[str]:
        return list(self._index.keys())
