from __future__ import annotations

import re
from pathlib import Path

from artifact_workflow_runtime.done_contract import DoneContract
from artifact_workflow_runtime.environment.models import EnvironmentPlan, EnvironmentPlanItem
from artifact_workflow_runtime.models import ContextPacket, Task
from artifact_workflow_runtime.state.workspace import infer_workspace_root_from_text


_BOOTSTRAP_CANDIDATES = (
    "dotnet-install.sh",
    "scripts/bootstrap.sh",
    "scripts/setup.sh",
    "scripts/install.sh",
    "scripts/bootstrap_freeplane.sh",
    "scripts/setup_freeplane.sh",
    "scripts/install_freeplane.sh",
    "misc/scripts/start-xvfb-freeplane-env.sh",
    "misc/scripts/stop-xvfb-freeplane-env.sh",
    "misc/scripts/run-freeplane-csharp-smoke-test.sh",
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
    "misc/scripts/run-freeplane-csharp-smoke-test.sh",
    "dotnet --version",
    "protoc --version",
    "test -x /workspace/dotnet/grpc_csharp_plugin",
    "smoke.sh",
    "integration.sh",
)
_FREEPLANE_BOOTSTRAP_CANDIDATES = (
    "misc/scripts/start-xvfb-freeplane-env.sh",
    "misc/scripts/stop-xvfb-freeplane-env.sh",
    "misc/scripts/run-freeplane-csharp-smoke-test.sh",
    "scripts/install_freeplane.sh",
    "scripts/setup_freeplane.sh",
    "scripts/bootstrap_freeplane.sh",
    "scripts/run_freeplane.sh",
)
_FREEPLANE_RUNTIME_CANDIDATES = (
    "misc/scripts/run-freeplane-csharp-smoke-test.sh",
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
            runtime_probe_command = self._discover_probe(root, text, runtime_candidates, requirement.name)
            # Only observed concrete repository paths are repo-supported bootstrap.
            # Guesses are retained as candidates for human/agent context, but they
            # must not become runnable QA bootstrap obligations.
            fallback_command = None if bootstrap_command else self._fallback_bootstrap_command(requirement.name, text)
            bootstrap_possible = bool(bootstrap_command)
            source = "repo_supported" if bootstrap_command else ("unverified_candidate" if fallback_command else None)
            if bootstrap_command is None:
                bootstrap_command = fallback_command
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
                if rel.startswith(("dotnet ", "protoc ", "test ")):
                    continue
                if (root / rel).exists():
                    return f"./{rel}"
        for rel in candidates:
            rel_lower = rel.lower()
            if rel_lower in context_text:
                return rel if rel.startswith(("dotnet ", "protoc ", "test ")) else f"./{rel}"
        # Pick up explicit shell-like paths mentioned in observation/context,
        # including concrete misc/scripts paths discovered by observation.
        pattern = r"(?:^|\s)((?:/workspace/project/|\./)?(?:misc/)?(?:scripts/)?[\w./-]*(?:bootstrap|setup|install|smoke|integration|run|xvfb|freeplane|dotnet-install)[\w./-]*\.sh)(?:\s|$)"
        for match in re.finditer(pattern, context_text):
            candidate = match.group(1)
            if candidate.startswith("/workspace/project/"):
                candidate = "./" + candidate.removeprefix("/workspace/project/")
            elif not candidate.startswith("./"):
                candidate = "./" + candidate
            normalized = candidate[2:]
            if normalized in candidates or any(token in normalized for token in ("bootstrap", "setup", "install", "smoke", "integration", "run", "xvfb", "freeplane", "dotnet-install")):
                return candidate
        return None

    def _discover_probe(self, root: Path | None, context_text: str, candidates: tuple[str, ...], requirement_name: str) -> str | None:
        command = self._discover_command(root, context_text, candidates)
        if command:
            return command
        name = requirement_name.lower()
        if any(marker in name for marker in ("dotnet", ".net", "csharp", "c#", "nuget")):
            return "dotnet --version"
        if "protoc" in name:
            return "protoc --version"
        if "grpc" in name and "plugin" in name:
            return "test -x /workspace/dotnet/grpc_csharp_plugin"
        if "freeplane" in name or "runtime" in name:
            return self._discover_command(root, context_text, _FREEPLANE_RUNTIME_CANDIDATES)
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
