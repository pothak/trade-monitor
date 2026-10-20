"""trade-monitor: a self-hosted framework for running your own trading strategies.

The engine core imports only the Python standard library. Third-party libraries
appear only at the edges (datafeeds, rendering). See spec/DEPENDENCIES.md.

The public entry points:

    from trade_monitor.engine import run                # batch scan
    from trade_monitor.live import LiveEngine           # streaming
    from trade_monitor.strategy.loader import discover   # plugin loading

Strategies depend on `trade_monitor_contracts`, not on this package.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
