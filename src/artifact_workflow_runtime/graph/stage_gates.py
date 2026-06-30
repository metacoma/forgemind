from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Any


class StageReadinessError(RuntimeError):
    """Raised when a graph node is invoked before its required state exists."""


@dataclass(frozen=True, slots=True)
class StageReadinessGate:
    """Small deterministic readiness gate for node preconditions.

    The runtime should fail at the layer boundary with a clear contract error,
    not with a later KeyError/Pydantic traceback from business logic.
    """

    def require(self, state: Mapping[str, Any], stage: str, *fields: str) -> None:
        missing = [field for field in fields if state.get(field) is None]
        if missing:
            raise StageReadinessError(f"Stage {stage!r} is not ready; missing state fields: {missing}")
