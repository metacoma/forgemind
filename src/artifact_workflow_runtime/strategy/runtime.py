from __future__ import annotations

from typing import Any, Mapping

from artifact_workflow_runtime.models.state import ControllerDecision, WorkflowStateSnapshot

from .advisor import LLMStrategyAdvisor, StrategyContextBuilder
from .arbitrator import StrategyArbitrator
from .governor import StrategyGovernor
from .models import LLMStrategyRecommendation, StrategyAdvisorStatus, StrategyDecision, StrategyId, StrategySelectionMode
from .signals import signals_from_snapshot


def append_strategy_decision(state: Mapping[str, Any], decision: StrategyDecision) -> list[dict[str, Any]]:
    return [*(state.get("strategy_decisions") or []), decision.model_dump(mode="json")]


def strategy_governor_for(services: Any) -> StrategyGovernor:
    return getattr(services, "strategy_governor", None) or StrategyGovernor()


def strategy_selection_mode_for(services: Any) -> StrategySelectionMode:
    raw = getattr(services, "strategy_selection_mode", None)
    if raw is None:
        raw = getattr(getattr(services, "strategy_config", None), "selection_mode", None)
    return StrategySelectionMode.coerce(raw)


def strategy_advisor_for(services: Any, governor: StrategyGovernor) -> LLMStrategyAdvisor | None:
    existing = getattr(services, "strategy_advisor", None)
    if existing is not None:
        return existing
    llm_backend = getattr(services, "llm_backend", None)
    artifact_store = getattr(services, "artifact_store", None)
    if llm_backend is None:
        return None
    model_override = _strategy_model_override(services)
    return LLMStrategyAdvisor(llm_backend, artifact_store, model_override=model_override)


def record_strategy_checkpoint(services: Any, state: Mapping[str, Any], *, checkpoint_stage: str) -> dict[str, Any]:
    """Synchronous rule-based strategy checkpoint used by existing tests and callers.

    Runtime graph nodes should use ``record_strategy_checkpoint_async`` so hybrid
    mode can call the optional advisor. This function deliberately remains a
    deterministic fallback surface.
    """

    snapshot = WorkflowStateSnapshot.from_graph_state(state)
    signals = signals_from_snapshot(snapshot, current_stage=checkpoint_stage)
    governor = strategy_governor_for(services)
    baseline = governor.decide(snapshot=snapshot, signals=signals)
    return _persist_strategy_decision(
        services,
        state,
        snapshot=snapshot,
        checkpoint_stage=checkpoint_stage,
        baseline_decision=baseline,
        final_decision=baseline,
        signals=signals,
        recommendation=None,
        validation_result=None,
        mode=StrategySelectionMode.RULE_BASED,
    )


async def record_strategy_checkpoint_async(services: Any, state: Mapping[str, Any], *, checkpoint_stage: str) -> dict[str, Any]:
    snapshot = WorkflowStateSnapshot.from_graph_state(state)
    signals = signals_from_snapshot(snapshot, current_stage=checkpoint_stage)
    governor = strategy_governor_for(services)
    mode = strategy_selection_mode_for(services)
    baseline = governor.decide(snapshot=snapshot, signals=signals)
    recommendation: LLMStrategyRecommendation | None = None
    validation_result = None
    final_decision = baseline

    if mode in {StrategySelectionMode.LLM_ASSISTED, StrategySelectionMode.HYBRID}:
        advisor = strategy_advisor_for(services, governor)
        if advisor is None:
            recommendation = LLMStrategyRecommendation(
                advisor_status=StrategyAdvisorStatus.DISABLED,
                reason="LLM strategy advisor is not configured; using deterministic baseline.",
            )
        else:
            context = StrategyContextBuilder(governor.catalog).build(snapshot=snapshot, signals=signals)
            recommendation = await advisor.recommend(context, task_id=snapshot.task.id)
        arbitrator = getattr(services, "strategy_arbitrator", None) or StrategyArbitrator()
        final_decision, validation_result = arbitrator.arbitrate(
            mode=mode,
            baseline=baseline,
            signals=signals,
            recommendation=recommendation,
        )

    return _persist_strategy_decision(
        services,
        state,
        snapshot=snapshot,
        checkpoint_stage=checkpoint_stage,
        baseline_decision=baseline,
        final_decision=final_decision,
        signals=signals,
        recommendation=recommendation,
        validation_result=validation_result,
        mode=mode,
    )


def _persist_strategy_decision(
    services: Any,
    state: Mapping[str, Any],
    *,
    snapshot: WorkflowStateSnapshot,
    checkpoint_stage: str,
    baseline_decision: StrategyDecision,
    final_decision: StrategyDecision,
    signals: Any,
    recommendation: LLMStrategyRecommendation | None,
    validation_result: Any | None,
    mode: StrategySelectionMode,
) -> dict[str, Any]:
    governor = strategy_governor_for(services)
    definition = governor.catalog.get(final_decision.selected_strategy)
    payload = {
        "mode": mode.value,
        "decision": final_decision.model_dump(mode="json"),
        "baseline_decision": baseline_decision.model_dump(mode="json"),
        "llm_recommendation": recommendation.model_dump(mode="json") if recommendation is not None else None,
        "validation_result": validation_result.model_dump(mode="json") if validation_result is not None else None,
        "signals": signals.model_dump(mode="json"),
        "strategy": definition.model_dump(mode="json"),
    }
    artifact = services.artifact_store.add_json(
        "strategy_decision",
        payload,
        metadata={
            "task_id": snapshot.task.id,
            "checkpoint_stage": checkpoint_stage,
            "selected_strategy": final_decision.selected_strategy.value,
            "strategy_mode": mode.value,
        },
    )
    artifact_ids = list(state.get("artifact_ids") or [])
    advisor_artifact_id = recommendation.raw_response_artifact_id if recommendation is not None else None
    for artifact_id in [advisor_artifact_id, artifact.id]:
        if artifact_id and artifact_id not in artifact_ids:
            artifact_ids.append(artifact_id)
    controller_decisions = [*(state.get("controller_decisions") or [])]
    controller_decisions.append(
        ControllerDecision(
            stage=f"strategy:{checkpoint_stage}",
            selected_next_stage=checkpoint_stage,
            reason=f"Strategy selected: {final_decision.selected_strategy.value}. {final_decision.reason}",
        ).model_dump(mode="json")
    )
    return {
        "active_strategy": final_decision.selected_strategy.value,
        "strategy_decisions": append_strategy_decision(state, final_decision),
        "artifact_ids": artifact_ids,
        "controller_decisions": controller_decisions,
    }


def merge_strategy_update(base: dict[str, Any], strategy_update: dict[str, Any]) -> dict[str, Any]:
    if not strategy_update:
        return base
    merged = dict(base)
    if "active_strategy" in strategy_update:
        merged["active_strategy"] = strategy_update["active_strategy"]
    if "strategy_decisions" in strategy_update:
        merged["strategy_decisions"] = strategy_update["strategy_decisions"]
    if "artifact_ids" in strategy_update:
        artifact_ids: list[str] = []
        for artifact_id in [*(strategy_update.get("artifact_ids") or []), *(base.get("artifact_ids") or [])]:
            if artifact_id not in artifact_ids:
                artifact_ids.append(artifact_id)
        merged["artifact_ids"] = artifact_ids
    if "controller_decisions" in strategy_update:
        decisions: list[Any] = []
        for decision in [*(strategy_update.get("controller_decisions") or []), *(base.get("controller_decisions") or [])]:
            if decision not in decisions:
                decisions.append(decision)
        merged["controller_decisions"] = decisions
    return merged


def active_strategy_prompt_block(services: Any, state: Mapping[str, Any]) -> str:
    strategy_id = state.get("active_strategy") or StrategyId.DEFAULT.value
    governor = strategy_governor_for(services)
    try:
        definition = governor.catalog.get(strategy_id)
    except ValueError:
        definition = governor.catalog.get(StrategyId.DEFAULT)
    expectations = "\n".join(f"- {item}" for item in definition.verification_expectations) or "- Use standard verification expectations."
    preferences = "\n".join(
        f"- {stage}: {', '.join(items)}" for stage, items in definition.packet_stage_preferences.items()
    ) or "- No extra packet preferences."
    return (
        "Active strategy metadata (controller-selected, not a new role):\n"
        f"- id: {definition.id.value}\n"
        f"- description: {definition.description}\n"
        "- packet/stage preferences:\n"
        f"{preferences}\n"
        "- verification expectations:\n"
        f"{expectations}"
    )


def strategy_metadata(services: Any, state: Mapping[str, Any]) -> dict[str, Any]:
    strategy_id = str(state.get("active_strategy") or StrategyId.DEFAULT.value)
    try:
        definition = strategy_governor_for(services).catalog.get(strategy_id)
        return {
            "active_strategy": definition.id.value,
            "strategy_description": definition.description,
            "strategy_verification_expectations": list(definition.verification_expectations),
        }
    except ValueError:
        return {"active_strategy": StrategyId.DEFAULT.value}


def _strategy_model_override(services: Any) -> str | None:
    routing = getattr(services, "model_routing", None)
    llm_backend = getattr(services, "llm_backend", None)
    default_model = getattr(llm_backend, "default_model", None)
    if routing is None:
        return default_model
    try:
        return routing.resolve_direct_llm("strategy", default_model)
    except Exception:  # pragma: no cover - routing implementations are defensive but optional
        return default_model
