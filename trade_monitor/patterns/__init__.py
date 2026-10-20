"""Pattern detectors.

REGISTRY maps name -> factory. A strategy declares the pattern names it needs,
so adding a detector here makes it selectable without editing the engine.

To add a pattern:

    from .my_pattern import MyPattern
    from ..registry import Registry

    REGISTRY.register(
        "my_pattern",
        MyPattern,
        description="...",
        provides=("pivot", "stop", "measured_move_target", "strength"),
        config_schema={...},          # optional, for config validation
    )

The detector must implement the PatternDetector protocol: a `name` attribute and
`detect(series, **options) -> dict | None`. Return None when the pattern is not
present; return a geometry dict when it is.
"""

from __future__ import annotations

from ..registry import Registry
from .cup_handle import CupWithHandle

REGISTRY: Registry[CupWithHandle] = Registry()
REGISTRY.kind = "pattern"

REGISTRY.register(
    "cup_with_handle",
    CupWithHandle,
    description=(
        "O'Neil cup-with-handle base: rounded cup, shallow handle, pivot at the rim."
    ),
    version="0.1.0",
    provides=("pivot_price", "stop_reference", "measured_move_target", "strength"),
    config_schema={"type": "object", "properties": dict(CupWithHandle.DEFAULTS)},
)

__all__ = ["REGISTRY", "CupWithHandle"]
