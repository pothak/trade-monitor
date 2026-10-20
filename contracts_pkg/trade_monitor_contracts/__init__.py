"""trade-monitor-contracts: the strategy plugin interface.

This package exists so that a strategy author installs *only the interface*,
not the engine. A third-party strategy depends on this and nothing else:

    from trade_monitor_contracts import (
        Strategy, PatternDetector, DataFeed,
        Bar, PriceSeries, Fundamentals,
        Thesis, Evidence, Invalidation, ValidationResult,
    )

    class MyStrategy:
        name = "my_strategy"
        needs_fundamentals = False
        def score(self, series, fundamentals, patterns=None, **options): ...
        def thesis(self, *, series, fundamentals, patterns=None,
                   evidence, grade, confidence, **options): ...

Then drop the file in a scanned directory and trade-monitor loads it at
runtime. No engine import, no rebuild, no version coupling to the app.

The contract has no dependencies beyond the standard library, on purpose. That
is what lets it be installed standalone and stay stable while the engine
changes underneath it.
"""

from __future__ import annotations

from .contracts import (
    Bar,
    DataFeed,
    DeliveryAdapter,
    Evidence,
    Fundamentals,
    Grade,
    Invalidation,
    Market,
    PatternDetector,
    PriceSeries,
    QuarterEPS,
    Strategy,
    Thesis,
    ValidationResult,
    Validator,
    Verdict,
)

__version__ = "0.1.0"

__all__ = [
    "Bar",
    "PriceSeries",
    "QuarterEPS",
    "Fundamentals",
    "Evidence",
    "Invalidation",
    "Thesis",
    "ValidationResult",
    "Verdict",
    "Grade",
    "Market",
    "DataFeed",
    "PatternDetector",
    "Strategy",
    "Validator",
    "DeliveryAdapter",
]
