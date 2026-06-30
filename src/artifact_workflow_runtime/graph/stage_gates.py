from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Any

from artifact_workflow_runtime.models.state import WorkflowStateSnapshot, required_fields_for_stage


class StageReadinessError(RuntimeError):
    """Raised when a graph node is invoked before its required state exists."""


@dataclass(frozen=True, slots=True)
class StageReadinessGate:
    """Deterministic readiness gate for node preconditions.

    Stage preconditions are owned by the typed workflow-state contract in
    ``models.state``. Callers may pass extra fields for local, narrower checks,
    but an empty field list means "use the canonical stage contract" rather than
    silently allowing the stage to run.
    """

    def require(self, state: Mapping[str, Any] | WorkflowStateSnapshot, stage: str, *fields: str) -> None:
        required = tuple(fields) if fields else required_fields_for_stage(stage)
        missing = _missing_fields(state, required)
        if missing:
            raise StageReadinessError(f"Stage {stage!r} is not ready; missing state fields: {missing}")


def _missing_fields(state: Mapping[str, Any] | WorkflowStateSnapshot, fields: tuple[str, ...]) -> list[str]:
    missing: list[str] = []
    for field in fields:
        if isinstance(state, WorkflowStateSnapshot):
            value = getattr(state, field, None)
        else:
            value = state.get(field)
        if value is None:
            missing.append(field)
    return missing
