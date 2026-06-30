from __future__ import annotations

from .models import (
    LLMStrategyRecommendation,
    StrategyAdvisorStatus,
    StrategyCheckpointSignals,
    StrategyDecision,
    StrategyId,
    StrategySelectionMode,
    StrategyValidationResult,
)
from .validator import StrategyDecisionValidator


class StrategyArbitrator:
    """Combine deterministic baseline and optional LLM advice into one decision."""

    def __init__(self, validator: StrategyDecisionValidator | None = None) -> None:
        self.validator = validator or StrategyDecisionValidator()

    def arbitrate(
        self,
        *,
        mode: StrategySelectionMode,
        baseline: StrategyDecision,
        signals: StrategyCheckpointSignals,
        recommendation: LLMStrategyRecommendation | None,
    ) -> tuple[StrategyDecision, StrategyValidationResult | None]:
        if mode == StrategySelectionMode.RULE_BASED or recommendation is None:
            return baseline.model_copy(update={"constraints": _unique([*baseline.constraints, "strategy_mode=rule_based"])}), None

        validation = self.validator.validate(
            recommendation=recommendation,
            baseline_strategy=baseline.selected_strategy,
            signals=signals,
        )
        if recommendation.advisor_status != StrategyAdvisorStatus.SUCCESS or not validation.accepted:
            selected = validation.final_strategy
            reason = (
                f"Rule-based baseline selected {baseline.selected_strategy.value}: {baseline.reason} "
                f"LLM advisor recommended {recommendation.selected_strategy or '<none>'} "
                f"but validation rejected it ({validation.rejection_reason}); final strategy is {selected.value}."
            )
            return baseline.model_copy(
                update={
                    "selected_strategy": selected,
                    "reason": reason,
                    "confidence": _confidence_from_float(recommendation.confidence) if selected != baseline.selected_strategy else baseline.confidence,
                    "signals_used": _unique([*baseline.signals_used, "llm_recommendation_rejected"]),
                    "constraints": _unique([*baseline.constraints, *recommendation.constraints, *validation.policy_notes, f"strategy_mode={mode.value}", "deterministic_fallback_available"]),
                }
            ), validation

        selected = validation.final_strategy
        reason = (
            f"Rule-based baseline selected {baseline.selected_strategy.value}: {baseline.reason} "
            f"LLM advisor recommended {recommendation.selected_strategy}: {recommendation.reason} "
            f"Validation accepted the recommendation; final strategy is {selected.value}."
        )
        return baseline.model_copy(
            update={
                "selected_strategy": selected,
                "reason": reason,
                "confidence": _confidence_from_float(recommendation.confidence),
                "signals_used": _unique([*baseline.signals_used, *recommendation.signals_used]),
                "constraints": _unique([*baseline.constraints, *recommendation.constraints, *validation.policy_notes, f"strategy_mode={mode.value}", "llm_recommendation_validated", "deterministic_fallback_available"]),
            }
        ), validation


def _confidence_from_float(value: float) -> str:
    if value >= 0.75:
        return "high"
    if value >= 0.4:
        return "medium"
    return "low"


def _unique(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return out
