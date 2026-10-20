"""Rendering.

Chart and report renderers. A renderer turns a Thesis plus its source series
into a file, and never recomputes a judgment.

REGISTRY maps name -> factory.
"""

from __future__ import annotations

from ..registry import Registry

REGISTRY: Registry[object] = Registry()
REGISTRY.kind = "render"

__all__ = ["REGISTRY"]
