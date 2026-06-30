from __future__ import annotations

from pathlib import Path

from artifact_workflow_runtime.done_contract import DoneContract
from artifact_workflow_runtime.environment.models import EnvironmentPlan, EnvironmentPlanItem
from artifact_workflow_runtime.models import ContextPacket, Task


class EnvironmentDiscovery:
    def build_plan(
        self,
        *,
        task: Task,
        done_contract: DoneContract,
        context_packet: ContextPacket | None,
        workspace_branch: str,
        repo_root: str | None = None,
    ) -> EnvironmentPlan:
        text = (context_packet.text if context_packet is not None else "").lower()
        root = Path(repo_root) if repo_root else None
        items: list[EnvironmentPlanItem] = []
        for requirement in done_contract.environment_requirements:
            command = None
            bootstrap_possible = requirement.mode == "bootstrap_if_needed"
            source = requirement.source if bootstrap_possible else None
            if requirement.name == "freeplane_runtime":
                command = self._discover_freeplane_bootstrap(root, text)
                bootstrap_possible = command is not None or bootstrap_possible
                source = "repo_supported" if command else source
            items.append(
                EnvironmentPlanItem(
                    name=requirement.name,
                    required_for=[*done_contract.deliverables],
                    already_present=False,
                    bootstrap_possible=bootstrap_possible,
                    bootstrap_source=source,
                    bootstrap_command=command,
                    failure_mode="needs_environment" if not bootstrap_possible else "bootstrap_then_retry",
                )
            )
        return EnvironmentPlan(task_id=task.id, workspace_branch=workspace_branch, items=items)

    def _discover_freeplane_bootstrap(self, root: Path | None, context_text: str) -> str | None:
        candidates = [
            "./scripts/install_freeplane.sh",
            "./scripts/setup_freeplane.sh",
            "./scripts/bootstrap_freeplane.sh",
            "./scripts/run_freeplane.sh",
            "./gradlew installFreeplane",
        ]
        if root is not None and root.exists():
            for rel in ("scripts/install_freeplane.sh", "scripts/setup_freeplane.sh", "scripts/bootstrap_freeplane.sh", "scripts/run_freeplane.sh"):
                if (root / rel).exists():
                    return f"./{rel}"
        if "freeplane" in context_text and "script" in context_text:
            return candidates[0]
        return None
