"""Independent review of a thesis.

Validators implement the Validator protocol:

    name   str
    kind   "rule" | "model"
    validate(thesis) -> ValidationResult

A model validator may adjust confidence, but the engine guarantees a model can
never overturn a failing rule validator. See `canslim.engine.validate_thesis`.

REGISTRY maps name -> factory, so validators are selected by config.
"""

from __future__ import annotations

from ..registry import Registry

REGISTRY: Registry[object] = Registry()
REGISTRY.kind = "validator"

__all__ = ["REGISTRY"]
