from __future__ import annotations

from typing import Iterable

from artifact_workflow_runtime.models import (
    AcceptanceDecision,
    BlockerKind,
    ExecutionPlan,
    ExecutionResult,
    ObligationAnalysis,
    VerificationResult,
)
from artifact_workflow_runtime.models.state import WorkflowStateSnapshot

from .models import StrategyCheckpointSignals


_TEST_TERMS = ("test", "unit", "integration", "smoke", "lint", "build", "compile", "coverage", "regression", "behavior", "behaviour")
_DOC_TERMS = ("doc", "readme", "example", "snippet", "guide")
_CI_TERMS = ("ci", "workflow", "github actions", "pipeline", "build script", "job")
_BUILD_TERMS = ("build", "compile", "compiler", "assembly", "msbuild", "dotnet build")
_TEST_FAILURE_TERMS = ("test", "xunit", "nunit", "mstest", "dotnet test", "failed")
_RUNTIME_TERMS = ("environment", "runtime", "dependency", "sdk", "install", "bootstrap", "probe")

ALLOWED_STRATEGY_SIGNAL_NAMES: tuple[str, ...] = (
    "current_stage",
    "execution_status",
    "verification_status",
    "acceptance_status",
    "missing_evidence",
    "blockers",
    "blocker_kinds",
    "failed_check_levels",
    "explicit_failure_class",
    "active_packet_type",
    "active_packet_scope",
    "repair_count",
    "task_complexity_hint",
    "mutation_heavy",
    "has_tests_obligations",
    "has_docs_obligations",
    "has_ci_obligations",
    "failed_checks",
    "changed_files_summary",
    "task_description",
    "active_strategy",
    "previous_strategy_decisions",
)


def signals_from_snapshot(snapshot: WorkflowStateSnapshot, *, current_stage: str) -> StrategyCheckpointSignals:
    plan = snapshot.plan
    obligations = snapshot.obligations
    execution = snapshot.execution_result
    verification = snapshot.verification_result
    acceptance = snapshot.acceptance_decision
    active_packet = _active_packet(snapshot)
    active_packet_type = getattr(getattr(active_packet, "packet_type", None), "value", None)
    active_scope = getattr(active_packet, "scope", None)
    missing_evidence = _missing_evidence(execution, verification, acceptance, active_packet=active_packet)
    blockers = _blockers(execution, verification, acceptance, active_packet=active_packet)
    blocker_kinds = _blocker_kinds(execution, verification, acceptance)
    failed_check_levels = _failed_check_levels(execution, verification)
    obligation_text = _joined_obligation_text(plan, obligations, active_packet=active_packet)
    return StrategyCheckpointSignals(
        current_stage=current_stage,
        execution_status=_execution_status(execution),
        verification_status=_verification_status(verification),
        acceptance_status=_acceptance_status(acceptance),
        missing_evidence=missing_evidence,
        blockers=blockers,
        blocker_kinds=blocker_kinds,
        failed_check_levels=failed_check_levels,
        explicit_failure_class=_explicit_failure_class(execution, verification, blocker_kinds, failed_check_levels, blockers),
        active_packet_type=active_packet_type,
        active_packet_scope=active_scope,
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


def _active_packet(snapshot: WorkflowStateSnapshot):
    if snapshot.decomposition_plan is None or not snapshot.active_packet_id:
        return None
    for packet in snapshot.decomposition_plan.packets:
        if packet.packet_id == snapshot.active_packet_id:
            return packet
    return None


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


def _missing_evidence(
    execution: ExecutionResult | None,
    verification: VerificationResult | None,
    acceptance: AcceptanceDecision | None,
    *,
    active_packet,
) -> list[str]:
    items: list[str] = []
    if execution is not None:
        items.extend(_safe_list(getattr(execution, "structured_evidence", None) and getattr(execution.structured_evidence, "missing_evidence", [])))
    if verification is not None:
        items.extend(_safe_list(verification.missing_evidence))
        items.extend(_safe_list(verification.missing_test_levels))
        items.extend(_safe_list(verification.missing_obligations))
    if acceptance is not None:
        active_contract = getattr(active_packet, "local_contract", None)
        active_environment = set(_lower(item) for item in getattr(active_contract, "environment_nodes", []))
        active_work = set(_lower(item) for item in getattr(active_contract, "work_surfaces", []))
        active_levels = set(_lower(item) for item in getattr(active_contract, "verification_levels", []))
        for result in acceptance.obligation_results:
            reason = str(result.reason or "")
            obligation = str(result.obligation_name or "")
            combined = f"{obligation} {reason}".strip().lower()
            if "missing" not in combined:
                continue
            if active_packet is None:
                items.append(reason or obligation)
                continue
            if active_levels and any(level in combined for level in active_levels):
                items.append(reason or obligation)
                continue
            if active_environment and any(node in combined for node in active_environment):
                items.append(reason or obligation)
                continue
            if active_work and any(surface in combined for surface in active_work):
                items.append(reason or obligation)
                continue
    return _unique(items)


def _blockers(
    execution: ExecutionResult | None,
    verification: VerificationResult | None,
    acceptance: AcceptanceDecision | None,
    *,
    active_packet,
) -> list[str]:
    items: list[str] = []
    if execution is not None:
        items.extend(item.summary for item in execution.structured_evidence.blockers)
        if execution.stage_failure is not None:
            items.append(execution.stage_failure.summary)
    if verification is not None:
        items.extend(_safe_list(verification.checks_failed))
    if acceptance is not None and not acceptance.accepted:
        active_contract = getattr(active_packet, "local_contract", None)
        active_terms = {
            *[_lower(item) for item in getattr(active_contract, "environment_nodes", [])],
            *[_lower(item) for item in getattr(active_contract, "work_surfaces", [])],
            *[_lower(item) for item in getattr(active_contract, "verification_levels", [])],
        }
        for result in acceptance.obligation_results:
            text = str(result.reason or result.obligation_name)
            if not active_terms:
                items.append(text)
                continue
            lowered = text.lower()
            if any(term and term in lowered for term in active_terms):
                items.append(text)
    return _unique(items)


def _blocker_kinds(execution: ExecutionResult | None, verification: VerificationResult | None, acceptance: AcceptanceDecision | None) -> list[str]:
    items: list[str] = []
    if execution is not None:
        items.extend(getattr(item.blocker_kind, "value", item.blocker_kind) for item in execution.structured_evidence.blockers)
        if execution.stage_failure is not None:
            items.append(getattr(execution.stage_failure.failure_kind, "value", execution.stage_failure.failure_kind))
    if verification is not None and verification.missing_setup_steps:
        items.append(BlockerKind.MISSING_RUNTIME_PREREQUISITE.value)
    if acceptance is not None:
        items.extend(getattr(result.blocker_kind, "value", result.blocker_kind) for result in acceptance.obligation_results if getattr(result, "blocker_kind", None))
    return _unique([str(item) for item in items if str(item).strip()])


def _failed_check_levels(execution: ExecutionResult | None, verification: VerificationResult | None) -> list[str]:
    levels: list[str] = []
    if execution is not None:
        for test in execution.structured_evidence.tests:
            status = _lower(getattr(test, "status", None))
            if status in {"failed", "error"}:
                level = getattr(getattr(test, "level", None), "value", getattr(test, "level", None))
                if level:
                    levels.append(str(level))
        for command in execution.structured_evidence.commands_run:
            if command.exit_code in (None, 0):
                continue
            role = getattr(getattr(command, "role", None), "value", getattr(command, "role", None))
            if role in {"build", "unit_test", "integration_test", "smoke_test"}:
                levels.append(role.replace("_test", ""))
    if verification is not None:
        levels.extend(_safe_list(verification.missing_test_levels))
        for check in verification.checks_failed:
            lowered = _lower(check)
            if any(term in lowered for term in ("build", "compile")):
                levels.append("build")
            if "unit" in lowered:
                levels.append("unit")
            if "integration" in lowered:
                levels.append("integration")
            if "smoke" in lowered:
                levels.append("smoke")
    return _unique(levels)


def _explicit_failure_class(
    execution: ExecutionResult | None,
    verification: VerificationResult | None,
    blocker_kinds: list[str],
    failed_check_levels: list[str],
    blockers: list[str],
) -> str | None:
    lowered_blockers = [_lower(item) for item in blockers]
    if any(kind in blocker_kinds for kind in {BlockerKind.MISSING_ENVIRONMENT_DEPENDENCY.value, BlockerKind.MISSING_RUNTIME_PREREQUISITE.value, BlockerKind.INTEGRATION_ENVIRONMENT_UNAVAILABLE.value}):
        return "environment_gap"
    if execution is not None and execution.stage_failure is not None:
        return getattr(execution.stage_failure.failure_kind, "value", execution.stage_failure.failure_kind)
    if any(level in failed_check_levels for level in {"build", "unit", "unit_test"}) or _contains_any(lowered_blockers, _BUILD_TERMS):
        return "build_test_failure"
    if any(level in failed_check_levels for level in {"integration", "smoke", "runtime_probe"}) or _contains_any(lowered_blockers, _TEST_FAILURE_TERMS):
        return "verification_failure"
    if verification is not None and verification.missing_setup_steps:
        return "environment_gap"
    if _contains_any(lowered_blockers, _RUNTIME_TERMS):
        return "environment_gap"
    return None


def _joined_obligation_text(plan: ExecutionPlan | None, obligations: ObligationAnalysis | None, *, active_packet) -> str:
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
    active_contract = getattr(active_packet, "local_contract", None)
    if active_contract is not None:
        parts.extend(getattr(active_contract, "environment_nodes", []))
        parts.extend(getattr(active_contract, "work_surfaces", []))
        parts.extend(getattr(active_contract, "verification_levels", []))
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


def _lower(value: object) -> str:
    return str(value or "").strip().lower()
