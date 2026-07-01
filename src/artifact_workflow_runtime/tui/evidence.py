from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

ArtifactRow = Mapping[str, Any]


def artifact_stage(row: ArtifactRow) -> str:
    kind = str(row.get("kind") or "").lower()
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    request_id = str(metadata.get("request_id") or "").lower()
    if kind == "task":
        return "intake"
    if "classification" in kind:
        return "classify"
    if "route" in kind:
        return "route"
    if "context" in kind:
        return "build_context"
    if "contract" in kind:
        return "done_contract"
    if "plan" in kind and "decomposition" not in kind:
        return "plan"
    if "decomposition" in kind or "packet" in kind:
        return "execute"
    if "policy" in kind:
        return "policy"
    if "approval" in kind:
        return "approval"
    if "publish" in kind:
        return "publish"
    if "verification" in kind or "verify" in kind:
        return "verify"
    if "research" in kind or request_id.startswith("research"):
        return "research"
    if "obligation" in kind or request_id.startswith("oblig"):
        return "obligations"
    if "observation" in kind or "observe" in kind or request_id.startswith("observe"):
        return "observe"
    if "execution" in kind or "exec" in kind or request_id.startswith("exec"):
        return "execute"
    if "final" in kind:
        return "finalize"
    return "artifact"


def artifact_backing(row: ArtifactRow) -> str:
    raw_path = str(row.get("path") or "").strip()
    if not raw_path:
        return "inline"
    path = Path(raw_path)
    if path.exists() and path.is_file():
        return "file"
    if path.exists() and path.is_dir():
        return "dir"
    return "missing"


def artifact_summary_line(row: ArtifactRow) -> str:
    preview = str(row.get("preview") or "").strip().replace("\n", " ")
    if preview:
        return preview[:120]
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    for key in ("conversation_id", "request_id", "task_id", "plan_id"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return f"{key}: {value}"[:120]
    return "No inline preview recorded."


def artifact_summary_text(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return (
            "Evidence cockpit\n\n"
            "Artifacts, evidence bundles and structured outputs will appear here as the run progresses."
        )
    stage_counts: dict[str, int] = {}
    backing_counts: dict[str, int] = {}
    for row in rows:
        stage = artifact_stage(row)
        backing = artifact_backing(row)
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
        backing_counts[backing] = backing_counts.get(backing, 0) + 1
    top_stages = ", ".join(
        f"{stage}:{count}" for stage, count in sorted(stage_counts.items(), key=lambda item: (-item[1], item[0]))[:6]
    )
    backing = ", ".join(f"{kind}:{count}" for kind, count in sorted(backing_counts.items()))
    latest = rows[-1]
    return (
        "Evidence cockpit\n\n"
        f"items: {len(rows)}\n"
        f"by stage: {top_stages or 'n/a'}\n"
        f"storage: {backing or 'n/a'}\n"
        f"latest: {latest.get('kind', 'artifact')} / {artifact_stage(latest)}"
    )


def artifact_meta_text(row: ArtifactRow) -> str:
    raw_path = str(row.get("path") or "").strip()
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    lines = [
        "Selected evidence",
        "",
        f"what stage produced it: {artifact_stage(row)}",
        f"what kind of evidence it is: {row.get('kind', '')}",
        f"artifact id: {row.get('id', '')}",
        f"how it is stored: {artifact_backing(row)}",
        f"media type: {row.get('media_type', 'text/plain')}",
        f"created at: {row.get('created_at', '')}",
        f"resolved path: {raw_path or '<inline only>'}",
        "",
        f"why it matters: {artifact_summary_line(row)}",
    ]
    if metadata:
        lines += ["", "Metadata:"]
        for key in sorted(metadata.keys()):
            value = metadata.get(key)
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            lines.append(f"{key}: {value}")
    return "\n".join(lines)


def artifact_display_text(row: ArtifactRow) -> str:
    raw_path = str(row.get("path") or "").strip()
    preview = str(row.get("preview") or "")
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    sections = [
        f"Evidence stage: {artifact_stage(row)}",
        f"Kind: {row.get('kind', '')}",
        f"Artifact ID: {row.get('id', '')}",
        f"Storage: {artifact_backing(row)}",
        f"Media type: {row.get('media_type', 'text/plain')}",
        f"Created at: {row.get('created_at', '')}",
        f"Path: {raw_path or '<inline only>'}",
    ]
    if metadata:
        sections += ["", "Metadata:", json.dumps(metadata, ensure_ascii=False, indent=2)]
    if not raw_path:
        body = preview or "This evidence item was stored inline only. No separate file was written."
        sections += ["", "Evidence body:", body]
        return "\n".join(sections)
    path = Path(raw_path)
    try:
        if path.exists() and path.is_file():
            return "\n".join(sections + ["", "File contents:", path.read_text(encoding="utf-8", errors="replace")])
        if path.exists() and path.is_dir():
            body = preview or "A directory path was recorded for this evidence item, so there is no single file body to open."
            return "\n".join(sections + ["", "Directory-backed evidence:", body])
        body = preview or f"The indexed file path no longer exists on disk: {path}"
        return "\n".join(sections + ["", "Fallback preview:", body])
    except Exception as exc:  # pragma: no cover - interactive path
        body = preview or ""
        return "\n".join(sections + ["", f"Failed to read artifact file: {exc}", "", body])


def load_artifact_payload(row: ArtifactRow) -> dict[str, Any] | list[Any] | None:
    raw_path = str(row.get("path") or "").strip()
    if raw_path:
        path = Path(raw_path)
        try:
            if path.exists() and path.is_file():
                return json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            pass
    preview = str(row.get("preview") or "").strip()
    if preview.startswith("{") or preview.startswith("["):
        try:
            return json.loads(preview)
        except Exception:
            return None
    return None


def latest_artifact_payload(rows: list[dict[str, Any]], *kind_terms: str) -> dict[str, Any] | list[Any] | None:
    lowered = [term.lower() for term in kind_terms]
    for row in reversed(rows):
        kind = str(row.get("kind") or "").lower()
        if all(term in kind for term in lowered):
            payload = load_artifact_payload(row)
            if payload is not None:
                return payload
    return None
