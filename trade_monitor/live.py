"""Live engine: stream events in, bar-close decisions out.

This is the seam between the two cadences.

    per tick  (fast, cheap)     RollingWindow.apply + indicators
    per close (slow, decisive)  detect -> score -> thesis -> ledger

Nothing in `stream.py` knows about strategies, and nothing in `engine.py` knows
about streams. This module is the only place the two meet, which keeps both
sides simple and keeps the batch and live paths sharing one implementation.

Why decisions wait for the close
--------------------------------
A cup-with-handle is defined over daily bars. Running the detector on a
half-formed intraday bar reads incomplete geometry and emits signals that
disappear by the close. So the detector only ever sees closed bars.

Backtest parity is the other reason, and the more important one. The backtest
runs the detector once per day on daily bars. If live ran it per tick, live and
backtest would be different programs and every backtest number would stop
predicting live behaviour. One code path is what makes validation mean anything.

Market direction (M)
--------------------
M is a property of the market, not the symbol -- the 'M' letter in CAN SLIM
asks whether the *index* is trending. So it is computed once per day from the
benchmark and shared by every symbol's score. If each symbol computed M itself,
they could disagree whenever the index ticked between two closes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from trade_monitor_contracts import PriceSeries, Bar
from .engine import DEFAULT_LOOKBACK, detect_patterns, run_strategy, validate_thesis
from .stream import RollingWindow, StreamEvent, StreamHub


@dataclass
class LiveReport:
    """What happened on one symbol's bar close."""

    symbol: str
    day: str
    patterns: list[str] = field(default_factory=list)
    grade: str | None = None
    score: float | None = None
    thesis: Any | None = None
    skipped: str | None = None
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.errors:
            return f"{self.symbol} {self.day}: ERROR {self.errors[0]}"
        if self.skipped:
            return f"{self.symbol} {self.day}: skipped ({self.skipped})"
        pat = ",".join(self.patterns) or "none"
        txt = f"{self.symbol} {self.day}: patterns={pat}"
        if self.grade:
            txt += f" grade={self.grade} score={self.score:.1f}"
        return txt


class LiveEngine:
    """Turns a stream into decisions, one bar close at a time.

    Wiring:

        hub = StreamHub()
        live = LiveEngine(strategy="canslim", market="IN")
        hub.on_bar_close(live.on_bar_close)
        ...
        hub.ingest(StreamEvent(...))     # per tick
        live.on_bar_close(sym, day, w)   # fires automatically at close

    M is refreshed once per day: the first close of a new day triggers one
    benchmark fetch, and every symbol closing into that day reuses it.
    """

    def __init__(
        self,
        *,
        strategy: str = "canslim",
        market: str = "IN",
        lookback: int = DEFAULT_LOOKBACK,
        feed: Any | None = None,
        store: Any | None = None,
        ledger: Any | None = None,
        pattern_options: Mapping[str, dict[str, Any]] | None = None,
        strategy_options: Mapping[str, Any] | None = None,
        min_bars: int = 70,
        run_id: int | None = None,
    ) -> None:
        self.strategy = strategy
        self.market = market
        self.lookback = lookback
        self.feed = feed
        self.store = store
        self.ledger = ledger
        self.pattern_options = pattern_options or {}
        self.strategy_options = strategy_options or {}
        self.min_bars = min_bars
        self.run_id = run_id

        self._m_day: str | None = None
        self._bench_series: PriceSeries | None = None
        self._strategy_obj: Any | None = None
        self.reports: list[LiveReport] = []
        self.closed = 0
        self.decided = 0

    def _strategy(self) -> Any:
        """The strategy instance, created once and reused.

        Cached so every bar close scores with identical configuration, and so
        the pattern requirement is read from the strategy rather than guessed.
        """
        if self._strategy_obj is None:
            from .strategy import REGISTRY as STRATEGIES

            self._strategy_obj = STRATEGIES.create(self.strategy)
        return self._strategy_obj

    def _requires(self) -> tuple[str, ...]:
        """The strategy's declared pattern requirements.

        The batch engine gates on `strategy.requires`; live uses the same list
        or the two paths diverge (Principle P2). A strategy that declares no
        patterns is not gated, so it scores every name.
        """
        return tuple(getattr(self._strategy(), "requires", ()) or ())

    # -- market direction (M): once per day, shared ----------------------

    def _refresh_market(self, day: str) -> None:
        """Refresh the benchmark exactly once per trading day.

        Every symbol closing into `day` then shares this one series, so M is
        identical across the whole universe on that day.
        """
        if day == self._m_day:
            return
        self._m_day = day
        self._bench_series = None
        if self.feed is None:
            return
        bench = self.feed.index_for(self.market)
        if not bench:
            return
        try:
            self._bench_series = self.feed.price_series(bench, self.lookback)
            self._event(
                "market.refreshed",
                subject=bench,
                summary=f"M benchmark {bench} refreshed for {day}",
                payload={"day": day, "benchmark": bench},
            )
        except Exception as exc:  # noqa: BLE001 - M degrades to unknown
            self._event(
                "market.unavailable",
                subject=bench or "?",
                summary=f"benchmark unavailable: {type(exc).__name__}: {exc}",
            )

    # -- the bar-close hook ----------------------------------------------

    def on_bar_close(self, symbol: str, day: str, window: RollingWindow) -> LiveReport:
        """Called by StreamHub when a symbol's bar closes.

        Runs the detector and the strategy for that one symbol only. Low latency:
        a symbol is processed the moment its bar completes, without waiting for
        the rest of the universe.
        """
        self.closed += 1
        report = LiveReport(symbol=symbol, day=day)
        bars = self._window_bars(window)

        if len(bars) < self.min_bars:
            report.skipped = f"only {len(bars)} bars < {self.min_bars}"
            self.reports.append(report)
            return report

        try:
            self._refresh_market(day)
            series = PriceSeries(
                symbol=symbol,
                market=self.market,
                currency=self._currency(),
                bars=bars,
            )

            patterns = detect_patterns(
                series, self._requires(), pattern_options=self.pattern_options
            )
            report.patterns = list(patterns)

            self._event(
                "detect.hit" if patterns else "detect.miss",
                subject=symbol,
                summary=(f"{symbol} {day}: {','.join(report.patterns)}"
                         if patterns else f"{symbol} {day}: no pattern"),
                payload={"day": day, "patterns": report.patterns},
            )

            if self._requires() and not patterns:
                report.skipped = "no pattern"
                self.reports.append(report)
                return report

            info = self._score(series, patterns)
            report.grade = info.get("grade")
            report.score = info.get("score")
            report.thesis = info.get("thesis")
            self.decided += 1

            self._event(
                "score.computed",
                subject=symbol,
                summary=f"{symbol} {day}: grade={report.grade} score={report.score}",
                payload={
                    "day": day,
                    "grade": report.grade,
                    "score": report.score,
                    "patterns": report.patterns,
                },
            )
            if report.thesis is not None:
                self._event(
                    "thesis.created",
                    subject=symbol,
                    summary=f"{symbol} {day}: thesis for {report.patterns}",
                    payload={"day": day, "grade": report.grade},
                )

        except Exception as exc:  # noqa: BLE001 - one symbol never aborts the stream
            report.errors.append(f"{type(exc).__name__}: {exc}")
            self._event(
                "run.error",
                subject=symbol,
                summary=f"{symbol} {day}: {type(exc).__name__}: {exc}",
            )

        self.reports.append(report)
        return report

    # -- scoring ---------------------------------------------------------

    def _score(self, series: PriceSeries, patterns: Sequence[Any]) -> dict[str, Any]:
        """Run the strategy for one symbol.

        Uses the same STRATEGIES registry and the same strategy instance the
        batch path uses, so a runtime injected strategy behaves identically.
        """
        strat = self._strategy()
        fundamentals = None
        if getattr(strat, "needs_fundamentals", False) and self.feed is not None:
            try:
                fundamentals = self.feed.fundamentals(series.symbol)
            except Exception:  # noqa: BLE001 - score degrades, coverage drops
                fundamentals = None

        result = strat.score(
            series, fundamentals, patterns=patterns, **self.strategy_options
        )
        grade, score = self._unwrap_score(result)

        thesis = None
        if hasattr(strat, "thesis") and fundamentals is not None:
            try:
                thesis = strat.thesis(
                    series=series,
                    fundamentals=fundamentals,
                    patterns=patterns,
                    evidence=getattr(strat, "last_evidence", []),
                    grade=grade,
                    confidence=getattr(strat, "last_confidence", 0.0),
                    **self.strategy_options,
                )
            except Exception:  # noqa: BLE001
                thesis = None
        return {"grade": grade, "score": score, "thesis": thesis}

    @staticmethod
    def _unwrap_score(result: Any) -> tuple[str | None, float | None]:
        """Strategies may return a grade string, a (grade, score) tuple, or an
        object with .grade/.score. Accept all three."""
        if result is None:
            return None, None
        if isinstance(result, str):
            return result, None
        if isinstance(result, tuple):
            # The contract is score() -> (evidence, grade, confidence).
            # Also accept (grade, score) for simpler strategies.
            if len(result) >= 3:
                return result[1], _as_float(result[2])
            if len(result) == 2:
                first, second = result
                if isinstance(first, str):
                    return first, _as_float(second)
                return second, None
        grade = getattr(result, "grade", None)
        score = getattr(result, "score", None) or getattr(result, "confidence", None)
        return grade, _as_float(score)

    # -- helpers ---------------------------------------------------------

    def _currency(self) -> str:
        return {"IN": "INR", "US": "USD"}.get(self.market, "USD")

    @staticmethod
    def _window_bars(window: RollingWindow) -> list[Bar]:
        bars: list[Bar] = []
        for b in window.to_bars():
            bars.append(
                Bar(
                    date=b["date"],
                    open=b["open"],
                    high=b["high"],
                    low=b["low"],
                    close=b["close"],
                    volume=b["volume"],
                )
            )
        return bars

    def _event(self, kind: str, *, subject: str, summary: str,
               payload: Mapping[str, Any] | None = None) -> None:
        if self.ledger is None:
            return
        try:
            self.ledger.write(
                kind, subject=subject, summary=summary,
                run_id=self.run_id, payload=dict(payload or {}),
            )
        except Exception:  # noqa: BLE001 - audit must never break the stream
            pass

    def summary(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "market": self.market,
            "closed_bars": self.closed,
            "decided": self.decided,
            "hits": sum(1 for r in self.reports if r.patterns),
            "errors": sum(1 for r in self.reports if r.errors),
            "skipped": sum(1 for r in self.reports if r.skipped),
            "m_day": self._m_day,
        }


def attach(hub: StreamHub, live: LiveEngine) -> LiveEngine:
    """Connect a live engine to a hub. Convenience so callers write one line."""
    hub.on_bar_close(live.on_bar_close)
    return live


def _as_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


__all__ = ["LiveEngine", "LiveReport", "attach"]
