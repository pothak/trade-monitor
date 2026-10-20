"""Persistence.

Three stores, all SQLite, each with one job:

  Store        engine state: runs, observations, theses, decisions, outcomes
  MarketStore  normalized market data, sliced by point in time
  Ledger       append-only audit trail: every event, forever

They can share one file or live side by side; nothing depends on them being
separate.
"""

from __future__ import annotations

from .ledger import Event, Ledger
from .marketstore import Bar1, MarketStore, PointInTimeError, Query
from .sqlite_store import Store

__all__ = [
    "Store", "MarketStore", "Query", "Bar1", "PointInTimeError", "Ledger", "Event",
]
