from __future__ import annotations

from artifact_workflow_runtime.models import Capability, ExecutionPlan, ObligationAnalysis, TaskClassification

_ALLOWED_INTENTS = {"implement", "modify", "investigate", "document", "verify"}


def effective_task_intent(classification: TaskClassification) -> str:
    intent = (classification.task_intent or "").strip().lower()
    return intent if intent in _ALLOWED_INTENTS else "investigate"


def plan_is_analysis_only(plan: ExecutionPlan) -> bool:
    text = " ".join([plan.summary, *plan.steps, *plan.success_criteria]).lower()
    analysis_markers = ("analyze", "design", "document", "outline", "instructions", "review", "draft plan")
    implementation_markers = ("implement", "add", "modify", "edit", "write code", "create file", "update build", "run test", "compile")
    has_analysis = any(marker in text for marker in analysis_markers)
    has_implementation = any(marker in text for marker in implementation_markers)
    return has_analysis and not has_implementation


def plan_intent_mismatch(classification: TaskClassification, plan: ExecutionPlan) -> str | None:
    expected = effective_task_intent(classification)
    raw_actual = (plan.task_intent or "").strip().lower()
    raw_deliverable = (plan.deliverable_kind or "").strip().lower()
    text = " ".join([plan.summary, *plan.steps, *plan.success_criteria]).lower()
    has_implementation_markers = any(
        marker in text
        for marker in ("implement", "add", "modify", "edit", "write code", "create", "update build", "run test", "compile", "fix")
    )
    actual = raw_actual if raw_actual in _ALLOWED_INTENTS else ""
    if actual in {"", "investigate"} and (plan.requires_mutation or plan.must_change_world or has_implementation_markers):
        actual = "implement"
    deliverable = raw_deliverable
    if deliverable in {"", "analysis"} and (plan.requires_mutation or plan.must_change_world or has_implementation_markers):
        deliverable = "repository_changes" if classification.execution_family.value == "repository_change" else "changes"
    if expected in {"implement", "modify"}:
        if actual not in {"implement", "modify"}:
            return f"Planner degraded a {expected} task into {actual or 'unknown'} intent."
        if deliverable in {"analysis", "documentation"}:
            return f"Planner produced {deliverable} deliverable for a {expected} task instead of real changes."
        if not plan.requires_mutation and not plan.must_change_world and not has_implementation_markers:
            return f"Planner marked a {expected} task as non-mutating, which conflicts with the requested outcome."
        if classification.execution_family.value == "repository_change" and not plan.expected_repo_changes and raw_deliverable == "documentation":
            return "Repository implementation plan does not declare expected repository changes."
        if plan_is_analysis_only(plan):
            return "Planner produced an analysis-only plan for an implementation task."
    return None


def append_artifact_id(artifact_ids: list[str] | None, artifact_id: str) -> list[str]:
    return [*(artifact_ids or []), artifact_id]


def publish_required(plan: ExecutionPlan) -> bool:
    return bool(plan.require_commit or plan.require_push or Capability.REPO_CREATE_PR in plan.capabilities or plan.publication_steps)


def execution_capabilities(plan: ExecutionPlan) -> list[Capability]:
    return [cap for cap in plan.capabilities if cap is not Capability.REPO_CREATE_PR]


def publish_capabilities(plan: ExecutionPlan) -> list[Capability]:
    caps = list(execution_capabilities(plan))
    if (plan.require_commit or plan.require_push or plan.publication_steps or Capability.REPO_CREATE_PR in plan.capabilities) and Capability.GIT_WRITE not in caps:
        caps.append(Capability.GIT_WRITE)
    if (plan.publication_steps or Capability.REPO_CREATE_PR in plan.capabilities) and Capability.REPO_CREATE_PR not in caps:
        caps.append(Capability.REPO_CREATE_PR)
    return caps


def render_steps(title: str, steps: list[str]) -> str:
    if not steps:
        return f"{title}: none specified\n"
    return title + ":\n" + "\n".join(f"- {step}" for step in steps) + "\n"


def normalized_completion_status(passed: bool, missing_evidence: list[str], checks_passed: list[str], checks_failed: list[str], missing_test_levels: list[str], missing_setup_steps: list[str], missing_obligations: list[str], commit_required: bool, push_required: bool, commit_done: bool, push_done: bool, explicit_status: str | None = None) -> str:
    if explicit_status and explicit_status != "partially_completed":
        return explicit_status
    if passed and not missing_test_levels and not missing_setup_steps and not missing_obligations:
        return "completed"
    if passed and (push_required or commit_required) and (not push_done or not commit_done):
        return "verified_not_published"
    if not passed and (checks_passed or checks_failed or missing_test_levels or missing_obligations):
        return "partially_completed"
    return "blocked" if missing_evidence and not checks_passed else ("completed" if passed else "partially_completed")


def merge_plan_with_obligations(plan: ExecutionPlan, obligations: ObligationAnalysis) -> ExecutionPlan:
    def _union_strings(left: list[str], right: list[str]) -> list[str]:
        result: list[str] = []
        for item in [*left, *right]:
            normalized = str(item).strip()
            if normalized and normalized not in result:
                result.append(normalized)
        return result

    required_test_levels = _union_strings(plan.required_test_levels, obligations.required_test_levels)
    required_setup_steps = _union_strings(plan.required_setup_steps, obligations.required_setup_steps)
    environment_notes = _union_strings(plan.environment_notes, obligations.required_environment_conditions)
    discovered_requirements = [
        *obligations.completion_requirements,
        *[f"documentation: {item}" for item in obligations.required_documentation_updates],
        *[f"examples: {item}" for item in obligations.required_examples_updates],
        *[f"ci/build: {item}" for item in obligations.required_ci_updates],
        *[f"codegen/build tooling: {item}" for item in obligations.required_codegen_or_build_updates],
        *[f"affected surface: {item}" for item in obligations.affected_surfaces],
    ]
    verification_checks = _union_strings(plan.verification_checks, discovered_requirements)
    success_criteria = _union_strings(plan.success_criteria, discovered_requirements)
    require_commit = plan.require_commit or any(action in {"commit", "push", "create_pr", "wait_pr_checks", "fix_failing_pr_checks"} for action in obligations.required_publish_actions)
    require_push = plan.require_push or any(action in {"push", "create_pr", "wait_pr_checks", "fix_failing_pr_checks"} for action in obligations.required_publish_actions)
    publication_steps = _union_strings(plan.publication_steps, obligations.required_publish_actions)
    return plan.model_copy(update={
        "required_test_levels": required_test_levels,
        "required_setup_steps": required_setup_steps,
        "environment_notes": environment_notes,
        "verification_checks": verification_checks,
        "success_criteria": success_criteria,
        "require_commit": require_commit,
        "require_push": require_push,
        "publication_steps": publication_steps,
        "expected_repo_changes": _union_strings(plan.expected_repo_changes, [*obligations.required_documentation_updates, *obligations.required_examples_updates, *obligations.required_ci_updates, *obligations.required_codegen_or_build_updates]),
    })
