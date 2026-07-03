from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Iterable, Mapping

from artifact_workflow_runtime.models.core import ExecutionResult, ObservationResult, Task

_WORKSPACE_PATH_RE = re.compile(r"/workspace/[A-Za-z0-9_.@:+\-\/]+")
_REPO_MARKERS = (
    "/.github/",
    "/grpc/",
    "/scripts/",
    "/src/main/",
    "/src/test/",
    "/misc/",
    "/examples/",
)
_FILE_SUFFIXES = (
    ".cs",
    ".py",
    ".go",
    ".rs",
    ".kt",
    ".js",
    ".ts",
    ".rb",
    ".cpp",
    ".hpp",
    ".h",
    ".proto",
    ".yml",
    ".yaml",
    ".json",
    ".toml",
    ".xml",
    ".md",
    ".sh",
    ".csproj",
    ".sln",
)


def infer_workspace_root_from_text(text: str | None) -> str | None:
    """Infer an intended repository workspace root from task/evidence text.

    The runtime should not ask deterministic QA to inspect the controller's CWD.
    When the user or OpenHands evidence names an explicit /workspace/... repo,
    normalize that to the repository root, not to a nested file/directory.
    """

    for candidate in _workspace_candidates_from_text(text or ""):
        root = _normalize_workspace_candidate(candidate)
        if root:
            return root
    return None


def workspace_root_from_state(state: Mapping[str, Any], *, fallback: str | None = None) -> str | None:
    explicit = _clean_path(state.get("workspace_root"))
    if explicit:
        return explicit

    env_plan_raw = state.get("environment_plan")
    if isinstance(env_plan_raw, Mapping):
        env_root = _clean_path(env_plan_raw.get("workspace_root"))
        if env_root:
            return env_root

    execution_raw = state.get("execution_result")
    if execution_raw:
        try:
            execution = ExecutionResult.model_validate(execution_raw)
            root = infer_workspace_root_from_execution(execution)
            if root:
                return root
        except Exception:
            pass

    observation_raw = state.get("observation_result")
    if observation_raw:
        try:
            observation = ObservationResult.model_validate(observation_raw)
            root = infer_workspace_root_from_observation(observation)
            if root:
                return root
        except Exception:
            pass

    task_raw = state.get("task")
    if task_raw:
        try:
            task = Task.model_validate(task_raw)
            root = infer_workspace_root_from_text(task.description)
            if root:
                return root
        except Exception:
            if isinstance(task_raw, Mapping):
                root = infer_workspace_root_from_text(str(task_raw.get("description") or ""))
                if root:
                    return root

    return _clean_path(fallback)


def infer_workspace_root_from_environment_plan(plan: object | None) -> str | None:
    return _clean_path(getattr(plan, "workspace_root", None)) if plan is not None else None


def infer_workspace_root_from_execution(execution: ExecutionResult) -> str | None:
    candidates: list[str] = []
    evidence = execution.structured_evidence
    for command in evidence.commands_run:
        candidates.extend(_workspace_candidates_from_text(command.cwd or ""))
        candidates.extend(_workspace_candidates_from_text(command.command or ""))
    for file_item in [*evidence.files_changed, *evidence.files_observed]:
        candidates.extend(_workspace_candidates_from_text(file_item.path or ""))
    for fact in evidence.extracted_facts:
        candidates.extend(_workspace_candidates_from_text(f"{fact.subject} {fact.fact} {fact.source or ''}"))
    candidates.extend(_workspace_candidates_from_text(execution.summary))
    candidates.extend(_workspace_candidates_from_text(execution.evidence_text))
    return _first_normalized(candidates)


def infer_workspace_root_from_observation(observation: ObservationResult) -> str | None:
    evidence = observation.structured_evidence
    candidates: list[str] = []
    for command in evidence.commands_run:
        candidates.extend(_workspace_candidates_from_text(command.cwd or ""))
        candidates.extend(_workspace_candidates_from_text(command.command or ""))
    for file_item in [*evidence.files_changed, *evidence.files_observed]:
        candidates.extend(_workspace_candidates_from_text(file_item.path or ""))
    for fact in evidence.extracted_facts:
        candidates.extend(_workspace_candidates_from_text(f"{fact.subject} {fact.fact} {fact.source or ''}"))
    candidates.extend(_workspace_candidates_from_text(observation.summary))
    candidates.extend(_workspace_candidates_from_text(observation.evidence_text))
    return _first_normalized(candidates)


def _workspace_candidates_from_text(text: str) -> list[str]:
    if not text:
        return []
    return [match.group(0).rstrip("'\"`),.;:]") for match in _WORKSPACE_PATH_RE.finditer(text)]


def _first_normalized(candidates: Iterable[str]) -> str | None:
    for candidate in candidates:
        root = _normalize_workspace_candidate(candidate)
        if root:
            return root
    return None


def _normalize_workspace_candidate(candidate: str | None) -> str | None:
    value = _clean_path(candidate)
    if not value or not value.startswith("/workspace/"):
        return None
    for marker in _REPO_MARKERS:
        if marker in value:
            return value.split(marker, 1)[0]
    path = Path(value)
    if path.suffix in _FILE_SUFFIXES:
        return str(path.parent)
    parts = value.split("/")
    # Keep /workspace/<repo> for nested paths where no explicit repo marker was found.
    if len(parts) >= 4:
        return "/".join(parts[:3])
    return value


def _clean_path(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip().rstrip("'\"`),.;:]")
    if not cleaned:
        return None
    return cleaned


def collect_workspace_mutation_snapshot(workspace_root: str | None) -> dict[str, Any]:
    root = _clean_path(workspace_root)
    snapshot: dict[str, Any] = {
        "workspace_root": root,
        "git_tracked": False,
        "dirty": False,
        "changed_paths": [],
        "status_lines": [],
        "error": None,
    }
    if not root:
        return snapshot
    try:
        proc = subprocess.run(
            ["git", "-C", root, "status", "--porcelain=v1"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception as exc:  # pragma: no cover - defensive
        snapshot["error"] = str(exc)
        return snapshot
    if proc.returncode != 0:
        snapshot["error"] = (proc.stderr or proc.stdout or "git status failed").strip() or "git status failed"
        return snapshot
    snapshot["git_tracked"] = True
    lines = [line.rstrip() for line in proc.stdout.splitlines() if line.strip()]
    changed_paths: list[str] = []
    for line in lines:
        path = _porcelain_path(line)
        if path and path not in changed_paths:
            changed_paths.append(path)
    snapshot["status_lines"] = lines
    snapshot["changed_paths"] = changed_paths
    snapshot["dirty"] = bool(changed_paths)
    return snapshot


def _porcelain_path(line: str) -> str | None:
    if not line:
        return None
    body = line[3:] if len(line) > 3 else ""
    if not body:
        return None
    if " -> " in body:
        body = body.split(" -> ", 1)[1]
    cleaned = body.strip()
    return cleaned or None
