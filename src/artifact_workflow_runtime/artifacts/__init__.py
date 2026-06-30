from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from artifact_workflow_runtime.models import Artifact, new_id

JsonDict = dict[str, Any]

_SAFE_KIND_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


def _safe_kind(kind: str) -> str:
    value = _SAFE_KIND_RE.sub("_", kind.strip().lower()).strip("._-")
    return value or "artifact"


class ArtifactStore:
    """Small file-backed artifact registry.

    The store is intentionally boring: each runtime fact is persisted as a file
    plus an index record. Graph state carries artifact ids, not opaque free text,
    so later stages can rebuild context from this source of truth.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._index_path = self.root / "index.json"
        self._artifacts: dict[str, Artifact] = {}
        self._load_index()

    def _load_index(self) -> None:
        if not self._index_path.exists():
            return
        try:
            data = json.loads(self._index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        items = data.get("artifacts") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                artifact = Artifact.model_validate(item)
            except Exception:
                continue
            self._artifacts[artifact.id] = artifact

    def _write_index(self) -> None:
        payload = {"artifacts": [artifact.model_dump(mode="json") for artifact in self._artifacts.values()]}
        tmp = self._index_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self._index_path)

    def _new_path(self, kind: str, suffix: str) -> Path:
        artifact_id = new_id("artifact")
        name = f"{artifact_id}_{_safe_kind(kind)}{suffix}"
        return self.root / name

    def _register(self, *, kind: str, path: Path, media_type: str, text_preview: str | None, metadata: JsonDict | None) -> Artifact:
        artifact_id = path.name.split("_", 2)[:2]
        # Filename starts with artifact_<hex>; keep the exact id stable.
        if len(artifact_id) >= 2 and artifact_id[0] == "artifact":
            real_id = "_".join(artifact_id)
        else:
            real_id = new_id("artifact")
        artifact = Artifact(
            id=real_id,
            kind=kind,
            path=str(path.relative_to(self.root)),
            media_type=media_type,
            text_preview=text_preview,
            metadata=metadata or {},
        )
        self._artifacts[artifact.id] = artifact
        self._write_index()
        return artifact

    def add_text(self, kind: str, text: str, *, metadata: JsonDict | None = None, media_type: str = "text/plain") -> Artifact:
        path = self._new_path(kind, ".txt")
        path.write_text(text, encoding="utf-8")
        preview = text[:500] if text else ""
        return self._register(kind=kind, path=path, media_type=media_type, text_preview=preview, metadata=metadata)

    def add_json(self, kind: str, data: Any, *, metadata: JsonDict | None = None) -> Artifact:
        path = self._new_path(kind, ".json")
        text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
        path.write_text(text, encoding="utf-8")
        preview = text[:500]
        return self._register(kind=kind, path=path, media_type="application/json", text_preview=preview, metadata=metadata)

    def get(self, artifact_id: str) -> Artifact:
        try:
            return self._artifacts[artifact_id]
        except KeyError as exc:
            raise KeyError(f"Unknown artifact id: {artifact_id}") from exc

    def list(self) -> list[Artifact]:
        return list(self._artifacts.values())

    def iter(self) -> Iterable[Artifact]:
        return self._artifacts.values()

    def resolve_path(self, artifact_id: str) -> Path:
        artifact = self.get(artifact_id)
        return self.root / artifact.path

    def read_text(self, artifact_id: str) -> str:
        path = self.resolve_path(artifact_id)
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return path.read_bytes().decode("utf-8", errors="replace")

    def read_json(self, artifact_id: str) -> Any:
        return json.loads(self.read_text(artifact_id))
