from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

from artifact_workflow_runtime.environment import EnvironmentPlan
from artifact_workflow_runtime.qa.models import QAExecutionItem, QAExecutionReport, QAPlan


class DeterministicQARunner:
    def run(self, *, plan: QAPlan, environment_plan: EnvironmentPlan | None = None, cwd: str | None = None) -> QAExecutionReport:
        items: list[QAExecutionItem] = []
        workdir = cwd or os.getcwd()
        for check in plan.checks:
            if check.kind == "command" and check.command:
                items.append(self._run_command(check_id=check.id, name=check.name, command=check.command, cwd=workdir))
                continue
            if check.kind == "runtime_proof":
                items.append(self._run_runtime_proof(check_id=check.id, name=check.name, environment_plan=environment_plan, cwd=workdir))
                continue
            if check.kind == "ci_config_check":
                items.append(self._run_ci_check(check_id=check.id, cwd=workdir))
                continue
            items.append(QAExecutionItem(check_id=check.id, name=check.name, kind=check.kind, status="declared", reason="Evidence-review check; no deterministic command to run."))
        summary = "; ".join(f"{item.name}={item.status}" for item in items) or "No QA checks executed."
        return QAExecutionReport(task_id=plan.task_id, plan_id=plan.id, items=items, summary=summary)

    def _run_command(self, *, check_id: str, name: str, command: str, cwd: str) -> QAExecutionItem:
        try:
            completed = subprocess.run(command, shell=True, cwd=cwd, text=True, capture_output=True, timeout=300)
            output = (completed.stdout or "") + (("\n" + completed.stderr) if completed.stderr else "")
            return QAExecutionItem(
                check_id=check_id,
                name=name,
                kind="command",
                status="passed" if completed.returncode == 0 else "failed",
                command=command,
                exit_code=completed.returncode,
                output=output[:8000],
                reason="Command executed by deterministic QA runner.",
            )
        except Exception as exc:  # pragma: no cover - defensive
            return QAExecutionItem(check_id=check_id, name=name, kind="command", status="blocked", command=command, output=str(exc), reason="Deterministic QA runner failed to execute command.")

    def _run_runtime_proof(self, *, check_id: str, name: str, environment_plan: EnvironmentPlan | None, cwd: str) -> QAExecutionItem:
        command = None
        if environment_plan is not None:
            for item in environment_plan.items:
                if item.bootstrap_command:
                    command = item.bootstrap_command
                    break
        if not command:
            return QAExecutionItem(check_id=check_id, name=name, kind="runtime_proof", status="blocked", reason="No bootstrap/runtime proof command available.")
        return self._run_command(check_id=check_id, name=name, command=command, cwd=cwd)

    def _run_ci_check(self, *, check_id: str, cwd: str) -> QAExecutionItem:
        workflows = Path(cwd) / ".github" / "workflows"
        if not workflows.exists():
            return QAExecutionItem(check_id=check_id, name="ci_config_check", kind="ci_config_check", status="failed", reason=".github/workflows directory is missing.")
        files = sorted(str(path.relative_to(workflows)) for path in workflows.glob("*.y*ml"))
        return QAExecutionItem(check_id=check_id, name="ci_config_check", kind="ci_config_check", status="passed" if files else "failed", output="\n".join(files), reason="Checked workflow directory for CI wiring.")
