from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from artifact_workflow_runtime.done_contract import DoneContract
from artifact_workflow_runtime.environment.models import EnvironmentPlan, EnvironmentPlanItem
from artifact_workflow_runtime.models import (
    CommandRole,
    ContextPacket,
    FileRole,
    ObservationResult,
    ObligationAnalysis,
    Task,
)
from artifact_workflow_runtime.state.workspace import infer_workspace_root_from_text


_SETUP_FILE_ROLES = {
    FileRole.SETUP_SCRIPT,
    FileRole.SMOKE_HARNESS,
    FileRole.INTEGRATION_HARNESS,
}
_RUNTIME_FILE_ROLES = {
    FileRole.RUNTIME_PROBE_SCRIPT,
    FileRole.SMOKE_HARNESS,
    FileRole.INTEGRATION_HARNESS,
}
_SETUP_COMMAND_ROLES = {
    CommandRole.OBSERVED_SETUP_PATH,
}
_RUNTIME_COMMAND_ROLES = {
    CommandRole.OBSERVED_RUNTIME_PROBE,
}


@dataclass(frozen=True, slots=True)
class _CommandCandidate:
    command: str
    resolution: str
    source: str
    source_kind: str
    path_key: str
    relevance_rank: int
    role_rank: int


class EnvironmentDiscovery:
    def build_plan(
        self,
        *,
        task: Task,
        done_contract: DoneContract,
        context_packet: ContextPacket | None,
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
        bootstrap_candidates = self._collect_candidates(
            observation=observation,
            root=root,
            relevant_paths=relevant_paths,
            file_roles=_SETUP_FILE_ROLES,
            command_roles=_SETUP_COMMAND_ROLES,
        )
        runtime_candidates = self._collect_candidates(
            observation=observation,
            root=root,
            relevant_paths=relevant_paths,
            file_roles=_RUNTIME_FILE_ROLES,
            command_roles=_RUNTIME_COMMAND_ROLES,
        )

        items: list[EnvironmentPlanItem] = []
        for requirement in done_contract.environment_requirements:
            bootstrap = bootstrap_candidates[0] if bootstrap_candidates else None
            runtime_probe = runtime_candidates[0] if runtime_candidates else None
            if bootstrap is None and runtime_probe is not None and requirement.mode == "bootstrap_if_needed":
                bootstrap = runtime_probe
            bootstrap_possible = bool(bootstrap) or requirement.mode == "bootstrap_if_needed"
            items.append(
                EnvironmentPlanItem(
                    name=requirement.name,
                    required_for=[*done_contract.deliverables],
                    already_present=False,
                    bootstrap_possible=bootstrap_possible,
                    bootstrap_source=bootstrap.source if bootstrap else requirement.source,
                    bootstrap_resolution=bootstrap.resolution if bootstrap else "none",
                    bootstrap_command=bootstrap.command if bootstrap else None,
                    bootstrap_source_kind=bootstrap.source_kind if bootstrap else None,
                    bootstrap_candidates=[candidate.command for candidate in bootstrap_candidates],
                    runtime_probe_resolution=runtime_probe.resolution if runtime_probe else "none",
                    runtime_probe_command=runtime_probe.command if runtime_probe else None,
                    runtime_probe_source_kind=runtime_probe.source_kind if runtime_probe else None,
                    runtime_probe_candidates=[candidate.command for candidate in runtime_candidates],
                    failure_mode="bootstrap_then_retry" if bootstrap_possible else "needs_environment",
                )
            )
        return EnvironmentPlan(task_id=task.id, workspace_branch=workspace_branch, workspace_root=inferred_root, items=items)

    def _collect_candidates(
        self,
        *,
        observation: ObservationResult | None,
        root: Path | None,
        relevant_paths: list[str],
        file_roles: set[FileRole],
        command_roles: set[CommandRole],
    ) -> list[_CommandCandidate]:
        if observation is None:
            return []
        candidates: list[_CommandCandidate] = []
        for item in observation.structured_evidence.files_observed:
            if item.role not in file_roles or not item.path:
                continue
            rel_path = _normalize_path(item.path, root=root)
            if rel_path is None:
                continue
            candidates.append(
                _CommandCandidate(
                    command=f"./{rel_path}",
                    resolution="observed_repo_path",
                    source="repo_supported",
                    source_kind=str(item.role.value if hasattr(item.role, 'value') else item.role),
                    path_key=rel_path,
                    relevance_rank=_path_relevance(rel_path, relevant_paths),
                    role_rank=_file_role_rank(item.role),
                )
            )
        for command in observation.structured_evidence.commands_run:
            if command.role not in command_roles or not command.command:
                continue
            candidates.append(
                _CommandCandidate(
                    command=command.command,
                    resolution="observed_context_command",
                    source="context_observed",
                    source_kind=str(command.role.value if hasattr(command.role, 'value') else command.role),
                    path_key=command.command,
                    relevance_rank=0,
                    role_rank=_command_role_rank(command.role),
                )
            )
        deduped: dict[str, _CommandCandidate] = {}
        for candidate in sorted(candidates, key=lambda item: (item.relevance_rank, item.role_rank, item.path_key)):
            deduped.setdefault(candidate.command, candidate)
        return list(deduped.values())


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
    for path in [*obligations.affected_surfaces, *obligations.required_setup_steps, *obligations.required_ci_updates, *obligations.required_examples_updates]:
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


def _command_role_rank(role: CommandRole | None) -> int:
    order = {
        CommandRole.OBSERVED_SETUP_PATH: 0,
        CommandRole.OBSERVED_RUNTIME_PROBE: 0,
        CommandRole.EXECUTED_SETUP: 1,
        CommandRole.EXECUTED_RUNTIME_PROBE: 1,
        CommandRole.OTHER: 9,
        None: 9,
    }
    return order.get(role, 9)
