"""Strategies.

The registry is populated at runtime, not by hardcoded imports. CAN SLIM ships
as a reference strategy and is loaded from `strategies/` like any other.

How strategies are defined
--------------------------
A strategy is written as a `.py` file in a scanned directory, exposing either:

    STRATEGY   = MyStrategy()
    STRATEGIES = {"name": MyStrategy(), ...}
    def register(registry): registry.register("name", MyStrategy, requires=(...))

See `loader.py` for discovery, injection and fault isolation.

Ordering
--------
`load_reference()` registers the strategy bundled with the package, so there is
always something to run even with no external directories. External directories
are loaded after, and may shadow it by name.
"""

from __future__ import annotations

from ..registry import Registry
from .can_slim import CanSlimStrategy

REGISTRY: Registry[object] = Registry()
REGISTRY.kind = "strategy"


def load_reference() -> None:
    """Register the bundled CAN SLIM strategy. Idempotent."""
    if "canslim" not in REGISTRY:
        REGISTRY.register(
            "canslim",
            CanSlimStrategy,
            description=(
                "CAN SLIM: C A N S L M I scored over a chart base, graded and ranked."
            ),
            version="0.1.0",
            requires=("cup_with_handle",),
            provides=("grade", "confidence", "evidence", "invalidation"),
        )


# Registered on import so `REGISTRY.create("canslim")` always works.
load_reference()

__all__ = ["REGISTRY", "CanSlimStrategy", "load_reference"]
