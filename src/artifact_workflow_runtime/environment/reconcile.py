from __future__ import annotations

import re
from typing import Iterable

from artifact_workflow_runtime.models import CommandEvidence, ExecutionResult

from .models import EnvironmentPlan, EnvironmentPlanItem


_DOTNET_VERSION_RE = re.compile(r"\b(\d+\.\d+\.\d+)\b")


class EnvironmentPlanReconciliation:
    """Reconcile declared environment requirements with actual execute evidence.

    EnvironmentPlan is a controller-owned state object. OpenHands can report
    commands and outputs, but the control plane must translate those commands
    into typed prerequisite state instead of leaving stale bootstrap fields.
    """

    def reconcile_execution(
        self,
        plan: EnvironmentPlan | None,
        execution: ExecutionResult | None,
        *,
        evidence_artifact_ids: Iterable[str] | None = None,
    ) -> tuple[EnvironmentPlan | None, list[dict[str, object]]]:
        if plan is None or execution is None:
            return plan, []

        changes: list[dict[str, object]] = []
        artifact_ids = list(evidence_artifact_ids or [])
        updated_items: list[EnvironmentPlanItem] = []
        commands = list(execution.structured_evidence.commands_run)
        blocker_text = "\n".join(blocker.summary for blocker in execution.structured_evidence.blockers).lower()

        for item in plan.items:
            updated = item
            previous = item.model_dump(mode="json")
            name_text = item.name.lower()

            dotnet_probe = _successful_command(commands, lambda cmd: _is_dotnet_probe(cmd.command))
            dotnet_bootstrap = _successful_command(commands, lambda cmd: "dotnet-install.sh" in cmd.command.lower())
            if _is_dotnet_item(name_text, item) and (dotnet_probe is not None or dotnet_bootstrap is not None):
                metadata = dict(item.metadata)
                if dotnet_bootstrap is not None:
                    metadata["bootstrap_command_evidence"] = dotnet_bootstrap.command
                probe_command = dotnet_probe.command if dotnet_probe is not None else (item.runtime_probe_command or "dotnet --version")
                resolved_version = _extract_version(dotnet_probe.output_excerpt if dotnet_probe is not None else None)
                if resolved_version is not None:
                    metadata["resolved_version"] = resolved_version
                updated = updated.model_copy(update={
                    "bootstrap_attempted": True,
                    "bootstrap_status": "success",
                    "runtime_usable": dotnet_probe is not None or item.runtime_usable,
                    "runtime_probe_command": probe_command,
                    "resolved_version": resolved_version or item.resolved_version,
                    "evidence_artifact_ids": _dedupe([*item.evidence_artifact_ids, *artifact_ids]),
                    "metadata": metadata,
                })

            successful_bootstrap = _successful_command(commands, lambda cmd: _matches_bootstrap_for_item(cmd.command, item))
            if successful_bootstrap is not None:
                updated = updated.model_copy(update={
                    "bootstrap_attempted": True,
                    "bootstrap_status": "success",
                    "evidence_artifact_ids": _dedupe([*updated.evidence_artifact_ids, *artifact_ids]),
                })

            successful_probe = _successful_command(commands, lambda cmd: _matches_probe_for_item(cmd.command, item))
            if successful_probe is not None and not _is_static_or_build_only_surrogate(successful_probe.command):
                updated = updated.model_copy(update={
                    "runtime_usable": True,
                    "runtime_probe_command": successful_probe.command,
                    "bootstrap_attempted": True if _looks_like_runtime_bootstrap(successful_probe.command) else updated.bootstrap_attempted,
                    "bootstrap_status": "success" if _looks_like_runtime_bootstrap(successful_probe.command) else updated.bootstrap_status,
                    "evidence_artifact_ids": _dedupe([*updated.evidence_artifact_ids, *artifact_ids]),
                })

            if _is_freeplane_item(name_text, item):
                attempted = _first_command(commands, lambda cmd: any(marker in cmd.command.lower() for marker in ("freeplane", "xvfb", "run-freeplane", "start-xvfb")))
                if attempted is not None:
                    update_data = {
                        "bootstrap_attempted": True,
                        "bootstrap_status": "success" if attempted.exit_code == 0 else "failed",
                        "evidence_artifact_ids": _dedupe([*updated.evidence_artifact_ids, *artifact_ids]),
                    }
                    if _is_runtime_smoke_command(attempted.command) and attempted.exit_code == 0:
                        update_data["runtime_usable"] = True
                        update_data["runtime_probe_command"] = attempted.command
                    updated = updated.model_copy(update=update_data)
                if any(marker in blocker_text for marker in ("freeplane", "display", "xvfb", "integration environment", "runtime unavailable")):
                    updated = updated.model_copy(update={"runtime_usable": False})

            if updated.model_dump(mode="json") != previous:
                changes.append({
                    "item": item.name,
                    "previous": previous,
                    "new": updated.model_dump(mode="json"),
                    "reason": "environment_plan reconciled from execution command evidence",
                    "execution_result_id": execution.id,
                })
            updated_items.append(updated)

        return plan.model_copy(update={"items": updated_items}), changes


def _is_dotnet_item(name_text: str, item: EnvironmentPlanItem) -> bool:
    text = " ".join([name_text, item.bootstrap_command or "", item.runtime_probe_command or "", *item.required_for]).lower()
    return any(marker in text for marker in (".net", "dotnet", "c#", "csharp", "nuget"))


def _is_freeplane_item(name_text: str, item: EnvironmentPlanItem) -> bool:
    text = " ".join([name_text, item.bootstrap_command or "", item.runtime_probe_command or "", *item.required_for]).lower()
    return any(marker in text for marker in ("freeplane", "xvfb", "display", "verification_runtime", "runtime_proof"))


def _successful_command(commands: list[CommandEvidence], predicate) -> CommandEvidence | None:
    for command in commands:
        if command.exit_code == 0 and predicate(command):
            return command
    return None


def _first_command(commands: list[CommandEvidence], predicate) -> CommandEvidence | None:
    for command in commands:
        if predicate(command):
            return command
    return None


def _is_dotnet_probe(command: str) -> bool:
    text = command.lower().strip()
    return bool(re.search(r"(^|[;&|()]|\s)dotnet\s+--version\b", text))


def _extract_version(output: str | None) -> str | None:
    if not output:
        return None
    match = _DOTNET_VERSION_RE.search(output)
    return match.group(1) if match else None


def _matches_bootstrap_for_item(command: str, item: EnvironmentPlanItem) -> bool:
    text = command.lower()
    configured = (item.bootstrap_command or "").lower().strip()
    if configured and configured in text:
        return True
    item_text = " ".join([item.name, *item.required_for]).lower()
    if "dotnet" in item_text and "dotnet-install.sh" in text:
        return True
    if "freeplane" in item_text and any(marker in text for marker in ("freeplane", "xvfb")):
        return True
    return False


def _matches_probe_for_item(command: str, item: EnvironmentPlanItem) -> bool:
    text = command.lower()
    configured = (item.runtime_probe_command or "").lower().strip()
    if configured and configured in text:
        return True
    item_text = " ".join([item.name, *item.required_for]).lower()
    if "dotnet" in item_text and _is_dotnet_probe(command):
        return True
    if any(marker in item_text for marker in ("freeplane", "runtime_proof", "integration")) and any(marker in text for marker in ("smoke", "integration", "freeplane", "grpc")):
        return True
    return False


def _looks_like_runtime_bootstrap(command: str) -> bool:
    text = command.lower()
    return any(marker in text for marker in ("bootstrap", "setup", "install", "start", "xvfb", "freeplane"))


def _is_runtime_smoke_command(command: str) -> bool:
    text = command.lower()
    return any(marker in text for marker in ("smoke", "integration", "run-freeplane", "freeplane-csharp-smoke"))


def _is_static_or_build_only_surrogate(command: str) -> bool:
    text = command.lower()
    if "bash -n" in text or "sh -n" in text or "syntax" in text:
        return True
    build_only = any(marker in text for marker in ("cmake --build", "gradle assemble", "./gradlew assemble", "mvn compile", "go build", "npm run build", "dotnet build"))
    actual_test = any(marker in text for marker in ("pytest", "go test", "cargo test", "mvn test", "gradle test", "./gradlew test", "npm test", "dotnet test", "smoke", "integration", "e2e"))
    return build_only and not actual_test


def _dedupe(items: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out
