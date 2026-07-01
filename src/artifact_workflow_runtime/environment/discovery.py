from __future__ import annotations

import re
from pathlib import Path

from artifact_workflow_runtime.done_contract import DoneContract
from artifact_workflow_runtime.environment.models import EnvironmentPlan, EnvironmentPlanItem
from artifact_workflow_runtime.models import ContextPacket, Task
from artifact_workflow_runtime.state.workspace import infer_workspace_root_from_text


_BOOTSTRAP_CANDIDATES = (
    "scripts/bootstrap.sh",
    "scripts/setup.sh",
    "scripts/install.sh",
    "scripts/bootstrap_freeplane.sh",
    "scripts/setup_freeplane.sh",
    "scripts/install_freeplane.sh",
    "bootstrap.sh",
    "setup.sh",
    "install.sh",
)
_RUNTIME_PROBE_CANDIDATES = (
    "scripts/smoke.sh",
    "scripts/integration.sh",
    "scripts/run_smoke.sh",
    "scripts/run_integration.sh",
    "scripts/run_freeplane.sh",
    "smoke.sh",
    "integration.sh",
)
_FREEPLANE_BOOTSTRAP_CANDIDATES = (
    "scripts/install_freeplane.sh",
    "scripts/setup_freeplane.sh",
    "scripts/bootstrap_freeplane.sh",
    "scripts/run_freeplane.sh",
)
_FREEPLANE_RUNTIME_CANDIDATES = (
    "scripts/smoke_freeplane.sh",
    "scripts/freeplane_smoke.sh",
    "scripts/run_freeplane_smoke.sh",
    "scripts/run_freeplane.sh",
    "scripts/integration_freeplane.sh",
)


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
    ) -> EnvironmentPlan:
        context_text = context_packet.text if context_packet is not None else ""
        text = context_text.lower()
        inferred_root = workspace_root or infer_workspace_root_from_text(task.description) or infer_workspace_root_from_text(context_text) or "/workspace/project"
        root = Path(repo_root or inferred_root) if (repo_root or inferred_root) else None
        items: list[EnvironmentPlanItem] = []
        for requirement in done_contract.environment_requirements:
            bootstrap_candidates = _FREEPLANE_BOOTSTRAP_CANDIDATES if requirement.name == "freeplane_runtime" else _BOOTSTRAP_CANDIDATES
            runtime_candidates = _FREEPLANE_RUNTIME_CANDIDATES if requirement.name == "freeplane_runtime" else _RUNTIME_PROBE_CANDIDATES
            bootstrap_command = self._discover_command(root, text, bootstrap_candidates)
            runtime_probe_command = self._discover_command(root, text, runtime_candidates)
            bootstrap_possible = bool(bootstrap_command) or requirement.mode == "bootstrap_if_needed"
            source = "repo_supported" if bootstrap_command else (requirement.source if bootstrap_possible else None)
            if bootstrap_possible and bootstrap_command is None:
                bootstrap_command = self._fallback_bootstrap_command(requirement.name, text)
            items.append(
                EnvironmentPlanItem(
                    name=requirement.name,
                    required_for=[*done_contract.deliverables],
                    already_present=False,
                    bootstrap_possible=bootstrap_possible,
                    bootstrap_source=source,
                    bootstrap_command=bootstrap_command,
                    runtime_probe_command=runtime_probe_command,
                    failure_mode="bootstrap_then_retry" if bootstrap_possible else "needs_environment",
                )
            )
        return EnvironmentPlan(task_id=task.id, workspace_branch=workspace_branch, workspace_root=inferred_root, items=items)

    def _discover_command(self, root: Path | None, context_text: str, candidates: tuple[str, ...]) -> str | None:
        if root is not None and root.exists():
            for rel in candidates:
                if (root / rel).exists():
                    return f"./{rel}"
        for rel in candidates:
            if rel.lower() in context_text:
                return f"./{rel}"
        # Pick up explicit shell-like paths mentioned in observation/context.
        pattern = r"(?:^|\s)(\./(?:scripts/)?(?:bootstrap|setup|install|smoke|integration|run)[\w./-]*\.sh)(?:\s|$)"
        for match in re.finditer(pattern, context_text):
            candidate = match.group(1)
            normalized = candidate[2:]
            if normalized in candidates or any(token in normalized for token in ("bootstrap", "setup", "install", "smoke", "integration", "run")):
                return candidate
        return None

    def _fallback_bootstrap_command(self, name: str, context_text: str) -> str | None:
        if name == "freeplane_runtime" or "freeplane" in context_text:
            return "./scripts/install_freeplane.sh"
        if "bootstrap" in context_text and "script" in context_text:
            return "./scripts/bootstrap.sh"
        if "setup" in context_text and "script" in context_text:
            return "./scripts/setup.sh"
        if "install" in context_text and "script" in context_text:
            return "./scripts/install.sh"
        return None
