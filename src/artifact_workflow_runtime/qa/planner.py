from __future__ import annotations

from artifact_workflow_runtime.done_contract import DoneContract
from artifact_workflow_runtime.environment import EnvironmentPlan
from artifact_workflow_runtime.models import ExecutionPlan, VerificationResult
from artifact_workflow_runtime.qa.models import QACheck, QAPlan


class QAPlanner:
    def build_plan(
        self,
        *,
        task_id: str,
        execution_plan: ExecutionPlan,
        done_contract: DoneContract,
        environment_plan: EnvironmentPlan | None = None,
    ) -> QAPlan:
        checks: list[QACheck] = []
        if environment_plan is not None:
            for item in environment_plan.items:
                if item.bootstrap_possible and not item.already_present:
                    checks.append(
                        QACheck(
                            name=f"bootstrap:{item.name}",
                            kind="bootstrap",
                            command=item.bootstrap_command,
                            reason="Repository-supported environment bootstrap must be attempted before runtime/integration proof is declared blocked.",
                        )
                    )
        for level in execution_plan.required_test_levels:
            checks.append(QACheck(name=level, kind="level", reason=f"Required test level from plan: {level}"))
        for check in execution_plan.verification_checks:
            checks.append(
                QACheck(
                    name=check,
                    kind="command" if _looks_like_command(check) else "evidence_review",
                    command=check if _looks_like_command(check) else None,
                    reason="Verification check from plan.",
                )
            )
        if "runtime_proof" in done_contract.deliverables and not any(item.name == "runtime_proof" for item in checks):
            command = None
            if environment_plan is not None:
                for item in environment_plan.items:
                    if item.runtime_probe_command:
                        command = item.runtime_probe_command
                        break
            checks.append(QACheck(name="runtime_proof", kind="runtime_proof", command=command, reason="DoneContract requires runtime proof distinct from bootstrap/setup."))
        if "ci_update_if_tests_added" in done_contract.deliverables and not any(item.name == "ci_config_check" for item in checks):
            checks.append(QACheck(name="ci_config_check", kind="ci_config_check", reason="DoneContract requires CI wiring for newly added checks."))
        return QAPlan(task_id=task_id, checks=_dedupe_checks(checks))


def _looks_like_command(value: str) -> bool:
    text = value.strip()
    starters = ("pytest", "ruff", "mypy", "python", "bash", "sh ", "./", "make", "cmake", "gradle", "./gradlew", "mvn", "go test", "cargo test", "npm ", "pnpm ", "yarn ", "opa ")
    return text.startswith(starters) or any(token in text for token in (" && ", " || ", " | ", "; "))


def _dedupe_checks(checks: list[QACheck]) -> list[QACheck]:
    seen: set[tuple[str, str, str | None]] = set()
    out: list[QACheck] = []
    for item in checks:
        key = (item.name, item.kind, item.command)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out
