from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from artifact_workflow_runtime.done_contract import DoneContract, EnvironmentRequirement
from artifact_workflow_runtime.environment.models import EnvironmentAction, EnvironmentPlan, EnvironmentPlanItem
from artifact_workflow_runtime.models import FileRole, ObservationResult, ObligationAnalysis, Task
from artifact_workflow_runtime.state.workspace import infer_workspace_root_from_text

_BOOTSTRAP_FILE_ROLES = {FileRole.SETUP_SCRIPT}
_RUNTIME_PROBE_FILE_ROLES = {
    FileRole.RUNTIME_PROBE_SCRIPT,
    FileRole.SMOKE_HARNESS,
    FileRole.INTEGRATION_HARNESS,
}


@dataclass(frozen=True, slots=True)
class _ActionCandidate:
    command: str
    resolution: str
    source: str
    source_kind: str
    file_path: str
    relevance_rank: int
    role_rank: int


class EnvironmentDiscovery:
    def build_plan(
        self,
        *,
        task: Task,
        done_contract: DoneContract,
        context_packet,
        workspace_branch: str,
        workspace_root: str | None = None,
        repo_root: str | None = None,
        observation: ObservationResult | None = None,
        obligations: ObligationAnalysis | None = None,
    ) -> EnvironmentPlan:
        context_text = context_packet.text if context_packet is not None else ""
        inferred_root = workspace_root or infer_workspace_root_from_text(task.description) or infer_workspace_root_from_text(context_text) or "/workspace/project"
        root = Path(repo_root or inferred_root) if (repo_root or inferred_root) else None
        relevant_paths = _relevant_paths(obligations)
        bootstrap_candidates = self._collect_file_candidates(
            observation=observation,
            root=root,
            relevant_paths=relevant_paths,
            file_roles=_BOOTSTRAP_FILE_ROLES,
        )
        probe_candidates = self._collect_file_candidates(
            observation=observation,
            root=root,
            relevant_paths=relevant_paths,
            file_roles=_RUNTIME_PROBE_FILE_ROLES,
        )

        items: list[EnvironmentPlanItem] = []
        for requirement in done_contract.environment_requirements:
            bootstrap_actions = [_to_action(candidate, requirement) for candidate in bootstrap_candidates]
            probe_actions = [_to_action(candidate, requirement) for candidate in probe_candidates]
            primary_bootstrap = bootstrap_actions[0] if bootstrap_actions else None
            primary_probe = probe_actions[0] if probe_actions else None
            bootstrap_possible = bool(primary_bootstrap) or requirement.mode == "required"
            items.append(
                EnvironmentPlanItem(
                    name=requirement.name,
                    dependency_kind=requirement.dependency_kind,
                    required_for=[*done_contract.deliverables],
                    applicable_packet_types=list(requirement.applicable_packet_types),
                    required_verification_levels=list(requirement.required_verification_levels),
                    already_present=False,
                    bootstrap_possible=bootstrap_possible,
                    bootstrap_source=primary_bootstrap.source if primary_bootstrap else requirement.source,
                    bootstrap_resolution=primary_bootstrap.resolution if primary_bootstrap else "none",
                    bootstrap_command=primary_bootstrap.command if primary_bootstrap else None,
                    bootstrap_source_kind=primary_bootstrap.source_kind if primary_bootstrap else None,
                    bootstrap_candidates=[candidate.command for candidate in bootstrap_candidates],
                    bootstrap_actions=bootstrap_actions,
                    runtime_probe_resolution=primary_probe.resolution if primary_probe else "none",
                    runtime_probe_command=primary_probe.command if primary_probe else None,
                    runtime_probe_source_kind=primary_probe.source_kind if primary_probe else None,
                    runtime_probe_candidates=[candidate.command for candidate in probe_candidates],
                    runtime_probe_actions=probe_actions,
                    failure_mode="bootstrap_then_retry" if primary_bootstrap or primary_probe else "needs_environment",
                )
            )
        return EnvironmentPlan(task_id=task.id, workspace_branch=workspace_branch, workspace_root=inferred_root, items=items)

    def _collect_file_candidates(
        self,
        *,
        observation: ObservationResult | None,
        root: Path | None,
        relevant_paths: list[str],
        file_roles: set[FileRole],
    ) -> list[_ActionCandidate]:
        if observation is None:
            return []
        candidates: list[_ActionCandidate] = []
        for item in observation.structured_evidence.files_observed:
            if item.role not in file_roles or not item.path:
                continue
            rel_path = _normalize_path(item.path, root=root)
            if rel_path is None:
                continue
            candidates.append(
                _ActionCandidate(
                    command=f"./{rel_path}",
                    resolution="observed_repo_path",
                    source="repo_supported",
                    source_kind=str(item.role.value if hasattr(item.role, "value") else item.role),
                    file_path=rel_path,
                    relevance_rank=_path_relevance(rel_path, relevant_paths),
                    role_rank=_file_role_rank(item.role),
                )
            )
        deduped: dict[str, _ActionCandidate] = {}
        for candidate in sorted(candidates, key=lambda item: (item.relevance_rank, item.role_rank, item.file_path)):
            deduped.setdefault(candidate.command, candidate)
        return list(deduped.values())


def _to_action(candidate: _ActionCandidate, requirement: EnvironmentRequirement) -> EnvironmentAction:
    return EnvironmentAction(
        command=candidate.command,
        resolution=candidate.resolution,
        source=candidate.source,
        source_kind=candidate.source_kind,
        file_path=candidate.file_path,
        packet_types=list(requirement.applicable_packet_types),
    )


def _normalize_path(path: str, *, root: Path | None) -> str | None:
    raw = str(path or "").strip().replace("\\", "/")
    if not raw:
        return None
    candidate = Path(raw)
    if candidate.is_absolute() and root is not None:
        try:
            return candidate.relative_to(root).as_posix()
        except ValueError:
            return None
    return raw[2:] if raw.startswith("./") else raw.lstrip("/")


def _relevant_paths(obligations: ObligationAnalysis | None) -> list[str]:
    if obligations is None:
        return []
    seen: list[str] = []
    for path in [*obligations.affected_surfaces, *obligations.required_ci_updates, *obligations.required_examples_updates]:
        normalized = _normalize_path(path, root=None)
        if normalized and normalized not in seen:
            seen.append(normalized)
    return seen


def _path_relevance(path: str, relevant_paths: list[str]) -> int:
    if not relevant_paths:
        return 1
    normalized = path.rstrip("/")
    for relevant in relevant_paths:
        rhs = relevant.rstrip("/")
        if normalized == rhs:
            return 0
        if normalized.startswith(rhs + "/") or rhs.startswith(normalized + "/"):
            return 1
    return 2


def _file_role_rank(role: FileRole | None) -> int:
    order = {
        FileRole.SETUP_SCRIPT: 0,
        FileRole.RUNTIME_PROBE_SCRIPT: 0,
        FileRole.SMOKE_HARNESS: 1,
        FileRole.INTEGRATION_HARNESS: 2,
        FileRole.CI_WORKFLOW: 3,
        FileRole.OTHER: 9,
        None: 9,
    }
    return order.get(role, 9)
