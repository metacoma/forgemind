from __future__ import annotations

from typing import Iterable

from artifact_workflow_runtime.models import AcceptanceDecision, ExecutionPlan, ExecutionResult, ObligationAnalysis, VerificationResult
from artifact_workflow_runtime.models.state import WorkflowStateSnapshot

from .models import StrategyCheckpointSignals


_TEST_TERMS = ("test", "unit", "integration", "smoke", "lint", "build", "compile", "coverage", "regression", "behavior", "behaviour")
_DOC_TERMS = ("doc", "readme", "example", "snippet", "guide")
_CI_TERMS = ("ci", "workflow", "github actions", "pipeline", "build script", "job")


def signals_from_snapshot(snapshot: WorkflowStateSnapshot, *, current_stage: str) -> StrategyCheckpointSignals:
    plan = snapshot.plan
    obligations = snapshot.obligations
    execution = snapshot.execution_result
    verification = snapshot.verification_result
    acceptance = snapshot.acceptance_decision
    missing_evidence = _missing_evidence(execution, verification, acceptance)
    blockers = _blockers(execution, verification, acceptance)
    obligation_text = _joined_obligation_text(plan, obligations)
    return StrategyCheckpointSignals(
        current_stage=current_stage,
        execution_status=_execution_status(execution),
        verification_status=_verification_status(verification),
        acceptance_status=_acceptance_status(acceptance),
        missing_evidence=missing_evidence,
        blockers=blockers,
        repair_count=len(snapshot.repair_results),
        task_complexity_hint=_complexity_hint(snapshot=snapshot, plan=plan, obligations=obligations),
        mutation_heavy=_mutation_heavy(snapshot=snapshot, plan=plan),
        has_tests_obligations=_contains_any([obligation_text], _TEST_TERMS),
        has_docs_obligations=_contains_any([obligation_text], _DOC_TERMS),
        has_ci_obligations=_contains_any([obligation_text], _CI_TERMS),
        metadata={
            "artifact_count": len(snapshot.artifact_ids),
            "strategy_decision_count": len(snapshot.strategy_decisions),
        },
    )


def _execution_status(execution: ExecutionResult | None) -> str | None:
    if execution is None:
        return None
    if execution.stage_failure is not None:
        return "failed"
    status = getattr(execution.execution_status, "value", execution.execution_status)
    if str(status).lower() in {"failed", "blocked"}:
        return str(status).lower()
    if not execution.ok:
        return "failed"
    return str(status or "succeeded").lower()


def _verification_status(verification: VerificationResult | None) -> str | None:
    if verification is None:
        return None
    if verification.passed is False or verification.checks_failed or verification.missing_evidence:
        return "failed"
    return "passed" if verification.passed else "unknown"


def _acceptance_status(acceptance: AcceptanceDecision | None) -> str | None:
    if acceptance is None:
        return None
    status = getattr(acceptance.status, "value", acceptance.status)
    return str(status or "").lower()


def _missing_evidence(execution: ExecutionResult | None, verification: VerificationResult | None, acceptance: AcceptanceDecision | None) -> list[str]:
    items: list[str] = []
    if execution is not None:
        items.extend(_safe_list(getattr(execution, "structured_evidence", None) and getattr(execution.structured_evidence, "missing_evidence", [])))
        text = str(getattr(execution, "evidence_text", ""))
        for marker in ("missing evidence", "missing_evidence"):
            if marker in text.lower():
                items.append(marker)
    if verification is not None:
        items.extend(_safe_list(verification.missing_evidence))
        items.extend(_safe_list(verification.missing_test_levels))
        items.extend(_safe_list(verification.missing_obligations))
    if acceptance is not None:
        for result in acceptance.obligation_results:
            reason = str(result.reason or "")
            if "missing" in reason.lower():
                items.append(reason)
    return _unique(items)


def _blockers(execution: ExecutionResult | None, verification: VerificationResult | None, acceptance: AcceptanceDecision | None) -> list[str]:
    items: list[str] = []
    if execution is not None:
        items.extend(item.summary for item in execution.structured_evidence.blockers)
        if execution.stage_failure is not None:
            items.append(execution.stage_failure.summary)
    if verification is not None:
        items.extend(_safe_list(verification.checks_failed))
    if acceptance is not None and not acceptance.accepted:
        items.extend(str(result.reason or result.obligation_name) for result in acceptance.obligation_results)
    return _unique(items)


def _joined_obligation_text(plan: ExecutionPlan | None, obligations: ObligationAnalysis | None) -> str:
    parts: list[str] = []
    if plan is not None:
        parts.extend(plan.required_test_levels)
        parts.extend(plan.verification_checks)
        parts.extend(plan.success_criteria)
        parts.extend(plan.expected_repo_changes)
        parts.extend(plan.steps)
    if obligations is not None:
        parts.extend(obligations.required_test_levels)
        parts.extend(obligations.required_setup_steps)
        parts.extend(obligations.required_documentation_updates)
        parts.extend(obligations.required_examples_updates)
        parts.extend(obligations.required_ci_updates)
    return "\n".join(str(item) for item in parts)


def _complexity_hint(*, snapshot: WorkflowStateSnapshot, plan: ExecutionPlan | None, obligations: ObligationAnalysis | None) -> str:
    score = 0
    if plan is not None:
        score += len(plan.steps) + len(plan.expected_repo_changes) + len(plan.verification_checks)
    if obligations is not None:
        score += len(obligations.required_test_levels) + len(obligations.required_ci_updates) + len(obligations.required_documentation_updates)
    if len(snapshot.artifact_ids) > 10:
        score += 2
    if score >= 12:
        return "high"
    if score >= 5:
        return "medium"
    return "low"


def _mutation_heavy(*, snapshot: WorkflowStateSnapshot, plan: ExecutionPlan | None) -> bool:
    if plan is not None:
        return bool(plan.requires_mutation or plan.must_change_world or len(plan.expected_repo_changes) >= 3)
    classification = snapshot.classification
    if classification is not None:
        return bool(classification.task_intent in {"implement", "modify"} or "repo_write" in {str(item) for item in classification.capabilities})
    return False


def _contains_any(values: Iterable[str], terms: Iterable[str]) -> bool:
    lowered_terms = tuple(term.lower() for term in terms)
    return any(any(term in str(value).lower() for term in lowered_terms) for value in values)


def _safe_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)] if str(value).strip() else []


def _unique(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return out
