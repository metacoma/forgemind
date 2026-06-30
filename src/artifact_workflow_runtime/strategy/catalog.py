from __future__ import annotations

from collections.abc import Iterable

from .models import StrategyDefinition, StrategyId


_MINIMAL_STRATEGIES: tuple[StrategyDefinition, ...] = (
    StrategyDefinition(
        id=StrategyId.DEFAULT,
        description="Use the normal controller lifecycle without extra strategy pressure.",
        applicable_when=["No strong failure, uncertainty, refactor, or evidence-gap signal is present."],
        packet_stage_preferences={"plan": ["follow typed obligations"], "execute": ["bounded implementation packet"]},
        verification_expectations=["Use standard evidence requirements from the done/acceptance contracts."],
    ),
    StrategyDefinition(
        id=StrategyId.MVP_FIRST,
        description="Produce a small working slice before expanding the implementation surface.",
        applicable_when=["Mutation-heavy task", "No stable working slice or execution evidence exists yet"],
        packet_stage_preferences={"plan": ["prioritize smallest complete slice"], "execute": ["make the minimal coherent change first"]},
        verification_expectations=["Require build/unit proof for the first working slice before broadening scope."],
    ),
    StrategyDefinition(
        id=StrategyId.BDD_INCREMENTAL,
        description="Drive the task through behavior/test evidence in small increments.",
        applicable_when=["Missing behavior evidence", "Missing tests", "Verification asks for stronger test proof"],
        packet_stage_preferences={"plan": ["express behavior expectations as checks"], "execute": ["add or update tests near each behavior change"]},
        verification_expectations=["Strengthen expectations around behavior, test, and regression evidence."],
    ),
    StrategyDefinition(
        id=StrategyId.SPIKE_THEN_HARDEN,
        description="Resolve unknown environment/API blockers first, then harden into final implementation.",
        applicable_when=["Environment blockers", "API unknowns", "Repository/runtime shape is uncertain"],
        packet_stage_preferences={"observe": ["collect targeted facts"], "execute": ["prefer bounded probe before broad mutation"]},
        verification_expectations=["Require explicit blocker resolution or environment-blocked evidence before acceptance."],
    ),
    StrategyDefinition(
        id=StrategyId.REPAIR_ONLY,
        description="Constrain the next packet to repairing known failures without expanding scope.",
        applicable_when=["Failed execution", "Failed verification", "Repair/re-entry after a failed gate"],
        packet_stage_preferences={"repair": ["change only what is needed for reported failures"], "execute": ["do not add new features while repairing"]},
        verification_expectations=["Require evidence that the failed checks were re-run or are explicitly blocked."],
    ),
    StrategyDefinition(
        id=StrategyId.SAFE_REFACTOR,
        description="Keep refactors behavior-preserving and verification-heavy.",
        applicable_when=["Task is primarily refactoring", "Architecture cleanup without requested feature expansion"],
        packet_stage_preferences={"plan": ["preserve public behavior"], "execute": ["small behavior-preserving edits"]},
        verification_expectations=["Require before/after compatible tests or strong no-behavior-change evidence."],
    ),
)


class StrategyCatalog:
    """Typed in-process catalog of supported strategy modes.

    The catalog is deliberately static and deterministic. Strategies are not
    prompts or agents; they are small control-plane metadata objects.
    """

    def __init__(self, strategies: Iterable[StrategyDefinition] | None = None) -> None:
        items = tuple(strategies or _MINIMAL_STRATEGIES)
        self._by_id: dict[StrategyId, StrategyDefinition] = {}
        for item in items:
            if item.id in self._by_id:
                raise ValueError(f"Duplicate strategy id: {item.id.value}")
            self._by_id[item.id] = item
        missing = [item.value for item in StrategyId if item not in self._by_id]
        if missing:
            raise ValueError(f"StrategyCatalog is missing required strategies: {missing}")

    def get(self, strategy_id: StrategyId | str) -> StrategyDefinition:
        key = StrategyId.coerce(strategy_id)
        try:
            return self._by_id[key]
        except KeyError as exc:  # pragma: no cover - constructor ensures completeness
            raise ValueError(f"Unknown strategy id: {key.value}") from exc

    def contains(self, strategy_id: StrategyId | str) -> bool:
        try:
            self.get(strategy_id)
            return True
        except ValueError:
            return False

    def list(self) -> list[StrategyDefinition]:
        return [self._by_id[item] for item in StrategyId]

    def ids(self) -> list[StrategyId]:
        return list(StrategyId)


DEFAULT_STRATEGY_CATALOG = StrategyCatalog()
