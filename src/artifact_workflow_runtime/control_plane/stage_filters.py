from __future__ import annotations

from artifact_workflow_runtime.models import ExecutionPlan


_PUBLISH_PHASE_TERMS = (
    "commit",
    "push",
    "pull request",
    " pr",
    "create_pr",
    "open_pull_request",
    "wait_pr_checks",
    "pr checks",
    "publish branch",
    "publish obligations",
)


def execute_phase_items(items: list[str]) -> list[str]:
    """Keep only obligations that belong to execute.

    The publish stage owns commit/push/PR/check-wait side effects. Execute may
    edit files and run build/unit/integration checks, but it must not receive
    publish obligations as success criteria or verification commands.
    """

    kept: list[str] = []
    for item in items:
        text = str(item).strip()
        lowered = text.lower()
        if not text:
            continue
        if any(term in lowered for term in _PUBLISH_PHASE_TERMS):
            continue
        kept.append(text)
    return kept


def execute_prompt_steps(plan: ExecutionPlan) -> list[str]:
    return execute_phase_items(list(plan.steps))


def execute_success_criteria(plan: ExecutionPlan) -> list[str]:
    return execute_phase_items(list(plan.success_criteria))


def execute_verification_commands(plan: ExecutionPlan) -> list[str]:
    return execute_phase_items(list(plan.verification_checks))
