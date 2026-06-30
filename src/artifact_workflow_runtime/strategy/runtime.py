from __future__ import annotations

from typing import Any, Mapping

from artifact_workflow_runtime.models.state import ControllerDecision, WorkflowStateSnapshot

from .governor import StrategyGovernor
from .models import StrategyDecision, StrategyId
from .signals import signals_from_snapshot


def append_strategy_decision(state: Mapping[str, Any], decision: StrategyDecision) -> list[dict[str, Any]]:
    return [*(state.get("strategy_decisions") or []), decision.model_dump(mode="json")]


def strategy_governor_for(services: Any) -> StrategyGovernor:
    return getattr(services, "strategy_governor", None) or StrategyGovernor()


def record_strategy_checkpoint(services: Any, state: Mapping[str, Any], *, checkpoint_stage: str) -> dict[str, Any]:
    snapshot = WorkflowStateSnapshot.from_graph_state(state)
    signals = signals_from_snapshot(snapshot, current_stage=checkpoint_stage)
    governor = strategy_governor_for(services)
    decision = governor.decide(snapshot=snapshot, signals=signals)
    definition = governor.catalog.get(decision.selected_strategy)
    artifact = services.artifact_store.add_json(
        "strategy_decision",
        {
            "decision": decision.model_dump(mode="json"),
            "signals": signals.model_dump(mode="json"),
            "strategy": definition.model_dump(mode="json"),
        },
        metadata={
            "task_id": snapshot.task.id,
            "checkpoint_stage": checkpoint_stage,
            "selected_strategy": decision.selected_strategy.value,
        },
    )
    artifact_ids = list(state.get("artifact_ids") or [])
    if artifact.id not in artifact_ids:
        artifact_ids.append(artifact.id)
    controller_decisions = [*(state.get("controller_decisions") or [])]
    controller_decisions.append(
        ControllerDecision(
            stage=f"strategy:{checkpoint_stage}",
            selected_next_stage=checkpoint_stage,
            reason=f"Strategy selected: {decision.selected_strategy.value}. {decision.reason}",
        ).model_dump(mode="json")
    )
    return {
        "active_strategy": decision.selected_strategy.value,
        "strategy_decisions": append_strategy_decision(state, decision),
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
