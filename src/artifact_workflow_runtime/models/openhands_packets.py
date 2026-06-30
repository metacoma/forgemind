from __future__ import annotations

# Compatibility extraction layer. The canonical implementations are still in
# ``core`` during the P0 split; downstream code should import from these
# domain modules so ``core`` can be physically split in later commits without
# changing call sites.
from .core import (
    OpenHandsRunFailure,
    EnvironmentBlocker,
    DiscoveredImpact,
    DiscoveredWorkSurface,
)

__all__ = [
    "OpenHandsRunFailure",
    "EnvironmentBlocker",
    "DiscoveredImpact",
    "DiscoveredWorkSurface",
]
