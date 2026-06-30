from __future__ import annotations

from typing import Iterable

from .catalog import DEFAULT_STRATEGY_CATALOG, StrategyCatalog
from .models import (
    LLMStrategyRecommendation,
    StrategyAdvisorStatus,
    StrategyCheckpointSignals,
    StrategyId,
    StrategyValidationResult,
)

_FAILURE_STATUSES = {"failed", "error", "blocked", "needs_repair", "policy_violation", "fail_code"}
_UNKNOWN_BLOCKER_TERMS = ("unknown", "environment", "runtime", "dependency", "api", "toolchain", "sdk", "install", "blocked")
_ALLOWED_SIGNAL_PREFIXES = {
    "current_stage",
    "execution_status",
    "verification_status",
    "acceptance_status",
    "missing_evidence",
    "blockers",
    "repair_count",
    "task_complexity_hint",
    "mutation_heavy",
    "has_tests_obligations",
    "has_docs_obligations",
    "has_ci_obligations",
    "failed_checks",
    "changed_files_summary",
    "default",
    "task_description",
}


class StrategyDecisionValidator:
    """Validate an optional LLM recommendation before it can affect state."""

    def __init__(self, catalog: StrategyCatalog | None = None) -> None:
        self.catalog = catalog or DEFAULT_STRATEGY_CATALOG

    def validate(
        self,
        *,
        recommendation: LLMStrategyRecommendation,
        baseline_strategy: StrategyId,
        signals: StrategyCheckpointSignals,
    ) -> StrategyValidationResult:
        fallback = baseline_strategy
        if recommendation.advisor_status != StrategyAdvisorStatus.SUCCESS:
            return StrategyValidationResult(
                accepted=False,
                final_strategy=fallback,
                fallback_strategy=fallback,
                rejection_reason=f"advisor_status={recommendation.advisor_status.value}",
                policy_notes=["advisor_error_fallback_to_rule_based"],
            )
        if not recommendation.selected_strategy:
            return _reject("missing selected_strategy", fallback)
        if not self.catalog.contains(recommendation.selected_strategy):
            return StrategyValidationResult(
                accepted=False,
                final_strategy=fallback,
                fallback_strategy=fallback,
                rejection_reason=f"unknown strategy: {recommendation.selected_strategy}",
                policy_notes=["llm_cannot_create_strategy", "fallback_to_rule_based"],
            )
        selected = StrategyId.coerce(recommendation.selected_strategy)
        if not (0.0 <= float(recommendation.confidence) <= 1.0):
            return _reject("confidence must be between 0.0 and 1.0", fallback)
        if not str(recommendation.reason or "").strip():
            return _reject("reason is required", fallback)
        unknown_signals = _unknown_critical_signals(recommendation.signals_used)
        if unknown_signals:
            return StrategyValidationResult(
                accepted=False,
                final_strategy=fallback,
                fallback_strategy=fallback,
                rejection_reason=f"unknown critical signals: {unknown_signals}",
                policy_notes=["recommendation_must_use_typed_checkpoint_signals", "fallback_to_rule_based"],
            )

        failure = _is_failed(signals.execution_status) or _is_failed(signals.verification_status)
        has_unknown_blockers = _contains_any(signals.blockers, _UNKNOWN_BLOCKER_TERMS)
        has_missing_evidence = bool(signals.missing_evidence)
        if failure and selected != StrategyId.REPAIR_ONLY:
            if selected == StrategyId.SPIKE_THEN_HARDEN and has_unknown_blockers:
                return StrategyValidationResult(
                    accepted=True,
                    final_strategy=selected,
                    fallback_strategy=fallback,
                    policy_notes=["failed_state_with_environment_unknowns_allows_spike_then_harden"],
                )
            return StrategyValidationResult(
                accepted=False,
                final_strategy=StrategyId.REPAIR_ONLY,
                fallback_strategy=fallback,
                rejection_reason="failed execution/verification requires repair_only unless environment/API blockers justify spike_then_harden",
                policy_notes=["hard_failure_policy_override", "downgrade_to_repair_only"],
            )
        if _is_failed(signals.verification_status) and has_missing_evidence and selected == StrategyId.DEFAULT:
            return StrategyValidationResult(
                accepted=False,
                final_strategy=StrategyId.REPAIR_ONLY,
                fallback_strategy=fallback,
                rejection_reason="default is not allowed when verification failed with missing evidence",
                policy_notes=["verification_missing_evidence_requires_non_default_strategy"],
            )
        return StrategyValidationResult(
            accepted=True,
            final_strategy=selected,
            fallback_strategy=fallback,
            policy_notes=["llm_recommendation_validated"],
        )


def _reject(reason: str, fallback: StrategyId) -> StrategyValidationResult:
    return StrategyValidationResult(
        accepted=False,
        final_strategy=fallback,
        fallback_strategy=fallback,
        rejection_reason=reason,
        policy_notes=["fallback_to_rule_based"],
    )


def _is_failed(value: object) -> bool:
    return str(value or "").strip().lower() in _FAILURE_STATUSES


def _contains_any(values: Iterable[str], terms: Iterable[str]) -> bool:
    lowered_terms = tuple(str(term).lower() for term in terms)
    return any(any(term in str(value).lower() for term in lowered_terms) for value in values)


def _unknown_critical_signals(signals_used: Iterable[str]) -> list[str]:
    unknown: list[str] = []
    for raw in signals_used:
        signal = str(raw or "").strip()
        if not signal:
            continue
        normalized = signal.split(":", 1)[0].strip().lower()
        if normalized not in _ALLOWED_SIGNAL_PREFIXES:
            unknown.append(signal)
    return unknown
