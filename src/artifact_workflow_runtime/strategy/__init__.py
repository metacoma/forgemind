from __future__ import annotations

from .models import (
    LLMStrategyRecommendation,
    StrategyAdvisorContext,
    StrategyAdvisorStatus,
    StrategyCheckpointSignals,
    StrategyDecision,
    StrategyDefinition,
    StrategyId,
    StrategySelectionMode,
    StrategyValidationResult,
)

__all__ = [
    "DEFAULT_STRATEGY_CATALOG",
    "StrategyCatalog",
    "StrategyGovernor",
    "StrategyContextBuilder",
    "LLMStrategyAdvisor",
    "StrategyArbitrator",
    "StrategyDecisionValidator",
    "StrategyId",
    "StrategySelectionMode",
    "StrategyAdvisorStatus",
    "StrategyDefinition",
    "StrategyDecision",
    "StrategyCheckpointSignals",
    "StrategyAdvisorContext",
    "LLMStrategyRecommendation",
    "StrategyValidationResult",
    "ALLOWED_STRATEGY_SIGNAL_NAMES",
    "signals_from_snapshot",
    "record_strategy_checkpoint",
    "record_strategy_checkpoint_async",
    "merge_strategy_update",
    "active_strategy_prompt_block",
    "strategy_metadata",
]


def __getattr__(name: str):
    if name in {"DEFAULT_STRATEGY_CATALOG", "StrategyCatalog"}:
        from .catalog import DEFAULT_STRATEGY_CATALOG, StrategyCatalog
        return {"DEFAULT_STRATEGY_CATALOG": DEFAULT_STRATEGY_CATALOG, "StrategyCatalog": StrategyCatalog}[name]
    if name == "StrategyGovernor":
        from .governor import StrategyGovernor
        return StrategyGovernor
    if name == "StrategyArbitrator":
        from .arbitrator import StrategyArbitrator
        return StrategyArbitrator
    if name == "StrategyDecisionValidator":
        from .validator import StrategyDecisionValidator
        return StrategyDecisionValidator
    if name in {"StrategyContextBuilder", "LLMStrategyAdvisor"}:
        from .advisor import LLMStrategyAdvisor, StrategyContextBuilder
        return {"StrategyContextBuilder": StrategyContextBuilder, "LLMStrategyAdvisor": LLMStrategyAdvisor}[name]
    if name in {"ALLOWED_STRATEGY_SIGNAL_NAMES", "signals_from_snapshot"}:
        from .signals import ALLOWED_STRATEGY_SIGNAL_NAMES, signals_from_snapshot
        return {"ALLOWED_STRATEGY_SIGNAL_NAMES": ALLOWED_STRATEGY_SIGNAL_NAMES, "signals_from_snapshot": signals_from_snapshot}[name]
    if name in {"record_strategy_checkpoint", "record_strategy_checkpoint_async", "merge_strategy_update", "active_strategy_prompt_block", "strategy_metadata"}:
        from .runtime import (
            active_strategy_prompt_block,
            merge_strategy_update,
            record_strategy_checkpoint,
            record_strategy_checkpoint_async,
            strategy_metadata,
        )
        return {
            "record_strategy_checkpoint": record_strategy_checkpoint,
            "record_strategy_checkpoint_async": record_strategy_checkpoint_async,
            "merge_strategy_update": merge_strategy_update,
            "active_strategy_prompt_block": active_strategy_prompt_block,
            "strategy_metadata": strategy_metadata,
        }[name]
    raise AttributeError(name)
