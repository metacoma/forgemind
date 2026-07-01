from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

from artifact_workflow_runtime.environment import EnvironmentPlan
from artifact_workflow_runtime.qa.models import QAExecutionItem, QAExecutionReport, QAPlan
from artifact_workflow_runtime.state.workspace import infer_workspace_root_from_environment_plan


class DeterministicQARunner:
    def run(self, *, plan: QAPlan, environment_plan: EnvironmentPlan | None = None, cwd: str | None = None) -> QAExecutionReport:
        items: list[QAExecutionItem] = []
        workdir = cwd or infer_workspace_root_from_environment_plan(environment_plan) or os.getcwd()
        workspace_problem = self._workspace_problem(workdir)
        for check in plan.checks:
            if workspace_problem is not None and check.kind in {"command", "bootstrap", "runtime_proof", "ci_config_check"}:
                items.append(
                    QAExecutionItem(
                        check_id=check.id,
                        name=check.name,
                        kind=check.kind,
                        status="blocked",
                        command=check.command,
                        reason=workspace_problem,
                    )
                )
                continue
            if check.kind == "command" and check.command:
                items.append(self._run_command(check_id=check.id, name=check.name, command=check.command, cwd=workdir))
                continue
            if check.kind == "bootstrap":
                items.append(self._run_bootstrap(check_id=check.id, name=check.name, command=check.command, cwd=workdir))
                continue
            if check.kind == "runtime_proof":
                items.append(self._run_runtime_proof(check_id=check.id, name=check.name, environment_plan=environment_plan, cwd=workdir))
                continue
            if check.kind == "ci_config_check":
                items.append(self._run_ci_check(check_id=check.id, cwd=workdir))
                continue
            items.append(QAExecutionItem(check_id=check.id, name=check.name, kind=check.kind, status="declared", reason="Evidence-review check; no deterministic command to run."))
        summary = "; ".join(f"{item.name}={item.status}" for item in items) or "No QA checks executed."
        return QAExecutionReport(task_id=plan.task_id, plan_id=plan.id, items=items, summary=summary, workspace_root=workdir)

    def _workspace_problem(self, cwd: str) -> str | None:
        path = Path(cwd)
        if not path.exists():
            return f"Workspace root {cwd!r} is not accessible to deterministic QA runner."
        if not path.is_dir():
            return f"Workspace root {cwd!r} is not a directory."
        return None

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
                reason=f"Command executed by deterministic QA runner in workspace {cwd}.",
            )
        except Exception as exc:  # pragma: no cover - defensive
            return QAExecutionItem(check_id=check_id, name=name, kind="command", status="blocked", command=command, output=str(exc), reason=f"Deterministic QA runner failed to execute command in workspace {cwd}.")

    def _run_bootstrap(self, *, check_id: str, name: str, command: str | None, cwd: str) -> QAExecutionItem:
        if not command:
            return QAExecutionItem(check_id=check_id, name=name, kind="bootstrap", status="blocked", reason="Bootstrap was required but no executable bootstrap command was resolved.")
        missing = self._missing_relative_executable(command, cwd)
        if missing is not None:
            return QAExecutionItem(
                check_id=check_id,
                name=name,
                kind="bootstrap",
                status="blocked",
                command=command,
                reason=f"Bootstrap command {missing!r} is not present in workspace {cwd}.",
            )
        item = self._run_command(check_id=check_id, name=name, command=command, cwd=cwd)
        return item.model_copy(update={"kind": "bootstrap"})

    def _run_runtime_proof(self, *, check_id: str, name: str, environment_plan: EnvironmentPlan | None, cwd: str) -> QAExecutionItem:
        command = None
        if environment_plan is not None:
            for item in environment_plan.items:
                if item.runtime_probe_command:
                    command = item.runtime_probe_command
                    break
        if not command:
            return QAExecutionItem(check_id=check_id, name=name, kind="runtime_proof", status="blocked", reason="No runtime/smoke proof command available after setup; bootstrap path existence is not runtime proof.")
        missing = self._missing_relative_executable(command, cwd)
        if missing is not None:
            return QAExecutionItem(
                check_id=check_id,
                name=name,
                kind="runtime_proof",
                status="blocked",
                command=command,
                reason=f"Runtime proof command {missing!r} is not present in workspace {cwd}.",
            )
        if self._is_static_or_build_only_surrogate(command):
            return QAExecutionItem(
                check_id=check_id,
                name=name,
                kind="runtime_proof",
                status="blocked",
                command=command,
                reason="Runtime/smoke proof cannot be satisfied by syntax-check or build-only command.",
            )
        item = self._run_command(check_id=check_id, name=name, command=command, cwd=cwd)
        return item.model_copy(update={"kind": "runtime_proof"})

    def _is_static_or_build_only_surrogate(self, command: str) -> bool:
        text = command.lower()
        if "bash -n" in text or "sh -n" in text or "syntax" in text:
            return True
        build_only = any(marker in text for marker in ("cmake --build", "gradle assemble", "./gradlew assemble", "mvn compile", "go build", "npm run build"))
        actual_test = any(marker in text for marker in ("pytest", "go test", "cargo test", "mvn test", "gradle test", "./gradlew test", "npm test", "run_smoke", "run smoke", "run_integration", "run integration", "pytest", "go test", "cargo test", "mvn test", "gradle test", "./gradlew test", "npm test", "e2e"))
        return build_only and not actual_test

    def _missing_relative_executable(self, command: str, cwd: str) -> str | None:
        try:
            first = shlex.split(command, posix=True)[0]
        except Exception:
            return None
        if first.startswith("./"):
            path = Path(cwd) / first[2:]
            if not path.exists():
                return first
        return None

    def _run_ci_check(self, *, check_id: str, cwd: str) -> QAExecutionItem:
        workflows = Path(cwd) / ".github" / "workflows"
        if not workflows.exists():
            return QAExecutionItem(
                check_id=check_id,
                name="ci_config_check",
                kind="ci_config_check",
                status="failed",
                reason=f".github/workflows directory is missing under workspace {cwd}.",
            )
        files = sorted(str(path.relative_to(workflows)) for path in workflows.glob("*.y*ml"))
        return QAExecutionItem(check_id=check_id, name="ci_config_check", kind="ci_config_check", status="passed" if files else "failed", output="\n".join(files), reason=f"Checked workflow directory for CI wiring under workspace {cwd}.")
