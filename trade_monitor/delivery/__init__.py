"""Delivery adapters.

Adapters implement the DeliveryAdapter protocol:

    name      str
    deliver(thesis, **options) -> bool

An adapter never fetches or recomputes data; it only renders a Thesis.
REGISTRY maps name -> factory, so destinations are selected by config.
"""

from __future__ import annotations

from ..registry import Registry

REGISTRY: Registry[object] = Registry()
REGISTRY.kind = "delivery"

__all__ = ["REGISTRY"]
