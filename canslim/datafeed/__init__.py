"""Data feeds."""

from __future__ import annotations

from .yfinance_feed import REGISTRY as YFINANCE_REGISTRY

REGISTRY = YFINANCE_REGISTRY

__all__ = ["REGISTRY"]
