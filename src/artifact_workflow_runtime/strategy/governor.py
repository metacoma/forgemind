from __future__ import annotations

from typing import Iterable

from artifact_workflow_runtime.models.state import WorkflowStateSnapshot

from .catalog import DEFAULT_STRATEGY_CATALOG, StrategyCatalog
from .models import StrategyCheckpointSignals, StrategyDecision, StrategyId


_FAILURE_STATUSES = {"failed", "error", "blocked", "needs_repair", "policy_violation", "fail_code"}
_TEST_EVIDENCE_TERMS = ("test", "unit", "integration", "smoke", "behavior", "behaviour", "regression", "bdd")
_UNKNOWN_BLOCKER_TERMS = ("unknown", "environment", "runtime", "dependency", "api", "toolchain", "sdk", "install", "blocked")
_REFACTOR_TERMS = ("refactor", "cleanup", "clean up", "simplify", "restructure", "rename", "stabilize")
_EXPLICIT_LOCAL_FAILURES = {"build_test_failure", "verification_failure", "test_failure"}
_ENVIRONMENT_FAILURES = {"environment_gap"}


class StrategyGovernor:
    """Deterministic rule-based strategy selector.

    It does not decide graph topology and never calls an LLM. The governor only
    selects the current methodology metadata that controller stages may attach
    to packets and artifacts.
    """

    def __init__(self, catalog: StrategyCatalog | None = None) -> None:
        self.catalog = catalog or DEFAULT_STRATEGY_CATALOG

    def decide(self, *, snapshot: WorkflowStateSnapshot, signals: StrategyCheckpointSignals) -> StrategyDecision:
        previous = StrategyId.coerce(snapshot.active_strategy) if snapshot.active_strategy else None
        selected, reason, confidence, used = self._select(snapshot=snapshot, signals=signals)
        if not self.catalog.contains(selected):
            raise ValueError(f"StrategyGovernor selected unknown strategy: {selected!r}")
        constraints = [
            "deterministic_rule_based",
            "no_llm_call",
            "no_new_role",
            "does_not_choose_graph_edge",
        ]
        return StrategyDecision(
            selected_strategy=selected,
            previous_strategy=previous,
            reason=reason,
            confidence=confidence,
            checkpoint_stage=signals.current_stage,
            signals_used=used,
            constraints=constraints,
        )

    def _select(self, *, snapshot: WorkflowStateSnapshot, signals: StrategyCheckpointSignals) -> tuple[StrategyId, str, str, list[str]]:
        execution_status = _lower(signals.execution_status)
        verification_status = _lower(signals.verification_status)
        acceptance_status = _lower(signals.acceptance_status)
        missing = [_lower(item) for item in signals.missing_evidence]
        blockers = [_lower(item) for item in signals.blockers]
        failure_class = _lower(signals.explicit_failure_class)
        active_packet_type = _lower(signals.active_packet_type)
        task_text = _lower(getattr(snapshot.task, "description", ""))

        if failure_class in _EXPLICIT_LOCAL_FAILURES:
            return (
                StrategyId.REPAIR_ONLY,
                "A concrete local build/test/verification failure is present; constrain the next packet to repairing the explicit failing surface before chasing broader missing evidence.",
                "high",
                _signals("explicit_failure_class", "failed_check_levels", "active_packet_type"),
            )

        if failure_class in _ENVIRONMENT_FAILURES and active_packet_type in {"setup", "integration", "test", "verification"}:
            return (
                StrategyId.SPIKE_THEN_HARDEN,
                "The active packet is blocked by an environment/runtime materialization gap; resolve the dependency path before hardening implementation work.",
                "high",
                _signals("explicit_failure_class", "active_packet_type", "blocker_kinds"),
            )

        if execution_status in _FAILURE_STATUSES or verification_status in _FAILURE_STATUSES:
            return (
                StrategyId.REPAIR_ONLY,
                "Execution or verification has failed; constrain the next packet to repairing known failures.",
                "high",
                _signals("execution_status", "verification_status"),
            )

        if acceptance_status in _FAILURE_STATUSES and signals.repair_count > 0:
            return (
                StrategyId.REPAIR_ONLY,
                "Acceptance failed after repair/re-entry; continue in repair-only mode.",
                "high",
                _signals("acceptance_status", "repair_count"),
            )

        if _contains_any(blockers, _UNKNOWN_BLOCKER_TERMS):
            return (
                StrategyId.SPIKE_THEN_HARDEN,
                "Current blockers point to environment/API/runtime uncertainty; resolve unknowns before hardening implementation.",
                "medium",
                _signals("blockers"),
            )

        if signals.has_tests_obligations and (signals.has_missing_evidence or _contains_any(missing, _TEST_EVIDENCE_TERMS)):
            return (
                StrategyId.BDD_INCREMENTAL,
                "Test or behavior obligations exist but evidence is missing; use incremental behavior-driven verification.",
                "high",
                _signals("missing_evidence", "has_tests_obligations"),
            )

        if _contains_any([task_text], _REFACTOR_TERMS):
            return (
                StrategyId.SAFE_REFACTOR,
                "Task text looks like a refactor/cleanup/stabilization request; preserve behavior and keep changes small.",
                "medium",
                _signals("task_description"),
            )

        has_working_slice = execution_status in {"succeeded", "passed", "ok", "completed"} or bool(snapshot.execution_result and snapshot.execution_result.ok)
        if signals.mutation_heavy and not has_working_slice:
            return (
                StrategyId.MVP_FIRST,
                "Task is mutation-heavy and no stable working slice is present yet; build the smallest coherent slice first.",
                "medium",
                _signals("mutation_heavy", "execution_status"),
            )

        return (
            StrategyId.DEFAULT,
            "No strong failure, evidence-gap, uncertainty, mutation-slice, or refactor signal was detected.",
            "medium",
            _signals("default"),
        )


def _lower(value: object) -> str:
    return str(value or "").strip().lower()


def _contains_any(values: Iterable[str], terms: Iterable[str]) -> bool:
    normalized_terms = tuple(_lower(term) for term in terms)
    return any(any(term in value for term in normalized_terms) for value in values)


def _signals(*names: str) -> list[str]:
    return list(dict.fromkeys(names))
