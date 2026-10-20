"""Startup bootstrap: load every plugin kind before the engine runs.

One call, used by the CLI and the server, so both see exactly the same set of
plugins. Fault-isolated: a broken plugin of one kind never stops the others.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def bootstrap_strategies(dirs: list[str | Path] | None = None) -> Any:
    from .strategy import REGISTRY as STRATEGIES
    from .strategy.loader import discover

    if dirs is None:
        dirs = [Path("strategies")]
    return discover(STRATEGIES, dirs)


def bootstrap_feeds(dirs: list[str | Path] | None = None) -> list[dict[str, Any]]:
    from .datafeed import discover_feeds

    return discover_feeds(directories=dirs)


def bootstrap_all(strategy_dirs: list[str | Path] | None = None) -> dict[str, Any]:
    """Discover strategies and spec feeds. Returns a small report."""
    strat_report = bootstrap_strategies(strategy_dirs)
    feeds = bootstrap_feeds()
    return {
        "strategies": strat_report.summary(),
        "feeds": [f.get("name") for f in feeds],
    }


__all__ = ["bootstrap_all", "bootstrap_strategies", "bootstrap_feeds"]
