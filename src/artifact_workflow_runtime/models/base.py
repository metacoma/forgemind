from __future__ import annotations

# Compatibility extraction layer. The canonical implementations are still in
# ``core`` during the P0 split; downstream code should import from these
# domain modules so ``core`` can be physically split in later commits without
# changing call sites.
from .core import (
    JsonDict,
    RuntimeModel,
    new_id,
    utc_now,
)

__all__ = [
    "JsonDict",
    "RuntimeModel",
    "new_id",
    "utc_now",
]
