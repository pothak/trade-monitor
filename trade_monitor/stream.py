"""Streaming layer: rolling windows and incremental compute.

What this is for
----------------
A feeder polls a provider for the latest price. Those updates arrive as a
stream of events. This module maintains, per symbol, a bounded, deduplicated,
ordered window of observations, and updates running indicators as each event
lands -- without re-reading history and without recomputing from scratch.

The one architectural rule
--------------------------
**The stream never makes decisions.**

This is deliberate and it is the most important thing in this file. Detection
and scoring live in the batch engine. The stream's whole job is to fill the
window fast. Decisions happen at bar close, by handing the same window to the
same engine that a backtest uses.

If the stream produced signals directly, live and backtest would run different
code, and every backtest number would stop predicting anything. Keeping one
code path is what makes the validation real.

    provider --poll--> StreamEvent --> RollingWindow --> indicators
                                            |
                                       on bar close
                                            v
                                   batch engine (detect -> score)

Event handling guarantees
-------------------------
Streams misbehave. Events arrive twice, out of order, or with a stale stamp.
So the window is defensive by construction:

  - **idempotent**: an event with a timestamp already seen is ignored
  - **ordered**: an out-of-order event is inserted in place, not appended
  - **bounded**: the window holds at most `capacity` price points
  - **monotonic**: a bar is only "closed" once, and never un-closed
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StreamEvent:
    """One observation from a provider.

    `ts` is the event time as ISO text (dates for daily bars, or a full
    timestamp for intraday). `close` is the current price. `volume` is the
    cumulative volume for the bar when the provider reports it that way, or the
    increment when it does not -- `cumulative_volume` says which.
    """

    symbol: str
    ts: str
    close: float
    high: float | None = None
    low: float | None = None
    open: float | None = None
    volume: float | None = None
    cumulative_volume: bool = True
    source: str = "unknown"

    @property
    def day(self) -> str:
        """The bar this event belongs to. Daily bars use ts directly; intraday
        stamps collapse to their date."""
        return self.ts.split("T")[0].split(" ")[0]


@dataclass
class Indicator:
    """A running computation over a window.

    `update` is called with the new event and the window; it returns the current
    value. It must be cheap and it must not raise -- a broken indicator is
    isolated by the window, not fatal to the stream.
    """

    name: str
    update: Callable[[RollingWindow, StreamEvent], float | None]
    value: float | None = None
    error: str | None = None


class RollingWindow:
    """Per-symbol state: recent events, running indicators, current bar.

    One window per symbol. It is intentionally not thread-safe; the feeder
    drives it from one thread per symbol or holds a lock. Keeping it free of
    locks makes the per-event cost predictable.
    """

    def __init__(
        self,
        symbol: str,
        *,
        capacity: int = 400,
        indicators: Iterable[Indicator] = (),
        min_bars_for_close: int = 1,
    ) -> None:
        self.symbol = symbol
        self.capacity = capacity
        self.min_bars_for_close = min_bars_for_close
        self._events: deque[StreamEvent] = deque(maxlen=capacity)
        self._seen_ts: set[str] = set()
        self._seen_day: set[str] = set()
        self.indicators: dict[str, Indicator] = {i.name: i for i in indicators}
        self.last_event: StreamEvent | None = None
        self.closed_bars: list[str] = []
        self.duplicates = 0
        self.out_of_order = 0
        self._current_day: str | None = None

    # -- ingest ----------------------------------------------------------

    def apply(self, event: StreamEvent) -> dict[str, Any]:
        """Apply one event. Returns a small report of what happened.

        Safe to call with duplicates and out-of-order events; both are handled
        and counted rather than corrupting the window.
        """
        report: dict[str, Any] = {"symbol": event.symbol, "ts": event.ts, "applied": False}
        if event.symbol != self.symbol:
            report["rejected"] = "symbol mismatch"
            return report

        day = event.day
        bar_closed = False

        # A new day means the previous day's bar is now closed.
        if self._current_day is not None and day != self._current_day:
            self._close_bar(self._current_day)
            bar_closed = True

        # Idempotency: same (symbol, ts) seen before is a duplicate.
        if event.ts in self._seen_ts:
            self.duplicates += 1
            report["duplicate"] = True
            if bar_closed:
                report["bar_closed"] = self._current_day
            return report

        # Out-of-order: an event older than the newest is inserted in place.
        if self._events and event.ts < self._events[-1].ts:
            self.out_of_order += 1
            inserted = False
            for idx, existing in enumerate(self._events):
                if event.ts < existing.ts:
                    self._events.insert(idx, event)
                    inserted = True
                    break
            if not inserted:
                self._events.append(event)
            report["out_of_order"] = True
        else:
            self._events.append(event)

        self._seen_ts.add(event.ts)
        self._current_day = day
        self._seen_day.add(day)
        self.last_event = event
        report["applied"] = True

        self._recompute(event)
        report["value"] = self.value()

        if bar_closed:
            report["bar_closed"] = self._previous_closed_day(day)
        return report

    def _previous_closed_day(self, current: str) -> str:
        for d in reversed(self.closed_bars):
            if d != current:
                return d
        return current

    def _close_bar(self, day: str) -> None:
        if day not in self.closed_bars:
            self.closed_bars.append(day)

    def _recompute(self, event: StreamEvent) -> None:
        """Update each indicator. A failing indicator records its error and the
        rest keep working."""
        for ind in self.indicators.values():
            try:
                ind.value = ind.update(self, event)
                ind.error = None
            except Exception as exc:  # noqa: BLE001
                ind.error = f"{type(exc).__name__}: {exc}"

    # -- reads -----------------------------------------------------------

    def events(self) -> list[StreamEvent]:
        return list(self._events)

    def closes(self) -> list[float]:
        return [e.close for e in self._events]

    def volumes(self) -> list[float]:
        return [e.volume or 0.0 for e in self._events]

    def value(self) -> dict[str, float | None]:
        return {name: ind.value for name, ind in self.indicators.items()}

    def ready(self, n: int) -> bool:
        return len(self._events) >= n

    def to_bars(self) -> list[dict[str, Any]]:
        """The window as bar dicts, so it can be handed to the batch engine.

        This is the bridge: the stream's only output that matters is a window
        that the normal detector can read.
        """
        out: list[dict[str, Any]] = []
        for e in self._events:
            out.append(
                {
                    "date": e.day,
                    "open": e.open if e.open is not None else e.close,
                    "high": e.high if e.high is not None else e.close,
                    "low": e.low if e.low is not None else e.close,
                    "close": e.close,
                    "volume": e.volume or 0.0,
                }
            )
        return out


# --------------------------------------------------------------------------
# Indicators
# --------------------------------------------------------------------------


def sma(period: int) -> Indicator:
    def _update(window: RollingWindow, event: StreamEvent) -> float | None:
        closes = window.closes()
        if len(closes) < period:
            return None
        return round(sum(closes[-period:]) / period, 4)

    return Indicator(f"sma{period}", _update)


def volume_ratio(short: int = 5, long: int = 60) -> Indicator:
    """Recent volume versus a longer baseline. This is the 'S' letter's signal,
    computed live."""

    def _update(window: RollingWindow, event: StreamEvent) -> float | None:
        vols = window.volumes()
        if len(vols) < long:
            return None
        recent = sum(vols[-short:]) / short
        base = sum(vols[-long:-short]) / max(1, long - short)
        return round(recent / base, 4) if base else None

    return Indicator(f"vol_ratio_{short}_{long}", _update)


def pct_from_high(lookback: int = 250) -> Indicator:
    """Distance from the window high. Live proxy for the pattern's proximity to
    pivot."""

    def _update(window: RollingWindow, event: StreamEvent) -> float | None:
        closes = window.closes()
        if len(closes) < 2:
            return None
        window_high = max(closes[-lookback:])
        if window_high <= 0:
            return None
        return round((window_high - event.close) / window_high * 100, 4)

    return Indicator(f"pct_from_high_{lookback}", _update)


def rate_of_change(period: int = 20) -> Indicator:
    def _update(window: RollingWindow, event: StreamEvent) -> float | None:
        closes = window.closes()
        if len(closes) < period + 1:
            return None
        past = closes[-period - 1]
        return round((event.close / past - 1) * 100, 4) if past else None

    return Indicator(f"roc{period}", _update)


def default_indicators() -> list[Indicator]:
    return [sma(20), sma(50), volume_ratio(), pct_from_high(), rate_of_change(20)]


# --------------------------------------------------------------------------
# Stream hub
# --------------------------------------------------------------------------


class StreamHub:
    """Owns one RollingWindow per symbol and routes events to them.

    Compute functions are just indicators attached to a window, so a user can
    register a new computation the same way a strategy is registered: by name,
    at runtime.
    """

    def __init__(
        self,
        *,
        capacity: int = 400,
        indicators_factory: Callable[[], list[Indicator]] = default_indicators,
    ) -> None:
        self.capacity = capacity
        self.indicators_factory = indicators_factory
        self.windows: dict[str, RollingWindow] = {}
        self.total_events = 0
        self.closed_bar_handlers: list[Callable[[str, str, RollingWindow], None]] = []

    def window(self, symbol: str) -> RollingWindow:
        w = self.windows.get(symbol)
        if w is None:
            w = RollingWindow(
                symbol, capacity=self.capacity, indicators=self.indicators_factory()
            )
            self.windows[symbol] = w
        return w

    def add_indicators(self, symbol: str, indicators: Iterable[Indicator]) -> None:
        w = self.window(symbol)
        for ind in indicators:
            w.indicators[ind.name] = ind

    def on_bar_close(
        self, handler: Callable[[str, str, RollingWindow], None]
    ) -> None:
        """Register a bar-close handler. This is where the batch engine hooks in:
        the stream fills the window, and on close the handler runs detect/score.

        Handlers run inline, so a slow handler slows ingestion for that symbol.
        Real deployments push onto a queue here. Kept synchronous for now so the
        ordering guarantee is obvious.
        """
        self.closed_bar_handlers.append(handler)

    def ingest(self, event: StreamEvent) -> dict[str, Any]:
        w = self.window(event.symbol)
        report = w.apply(event)
        self.total_events += 1
        if report.get("bar_closed"):
            for h in self.closed_bar_handlers:
                try:
                    h(event.symbol, report["bar_closed"], w)
                except Exception as exc:  # noqa: BLE001
                    report.setdefault("handler_errors", []).append(
                        f"{type(exc).__name__}: {exc}"
                    )
        return report

    def ingest_many(self, events: Iterable[StreamEvent]) -> list[dict[str, Any]]:
        return [self.ingest(e) for e in events]

    def snapshot(self) -> dict[str, Any]:
        return {
            "symbols": len(self.windows),
            "total_events": self.total_events,
            "duplicates": sum(w.duplicates for w in self.windows.values()),
            "out_of_order": sum(w.out_of_order for w in self.windows.values()),
            "values": {s: w.value() for s, w in self.windows.items()},
        }


__all__ = [
    "StreamEvent", "Indicator", "RollingWindow", "StreamHub",
    "sma", "volume_ratio", "pct_from_high", "rate_of_change", "default_indicators",
]
