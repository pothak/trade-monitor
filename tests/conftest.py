"""Shared test fixtures.

Two rules that make the suite trustworthy:

  1. **No network by default.** Everything here is synthetic and deterministic.
     A red test means the code is wrong, never that Yahoo is down.

  2. **Fixtures build real contracts.** Fakes implement the same Protocols the
     engine uses, so a test exercises the real code path, not a mock of it.

Real-source checks are marked `integration` and skipped unless asked for.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from datetime import date, timedelta

import pytest
from trade_monitor_contracts import (
    Bar,
    Fundamentals,
    PriceSeries,
    QuarterEPS,
)

# --------------------------------------------------------------------------
# Synthetic bars
# --------------------------------------------------------------------------


def make_bars(
    *,
    n: int = 300,
    start: float = 100.0,
    drift: float = 0.001,
    vol: float = 0.01,
    seed: int = 42,
    start_date: date = date(2024, 1, 1),
    volume_base: float = 1_000_000,
) -> list[Bar]:
    """A deterministic random walk of daily bars.

    Same seed always yields the same bars, so a test that depends on price
    geometry is reproducible.
    """
    rng = random.Random(seed)
    bars: list[Bar] = []
    price = start
    d = start_date
    for _ in range(n):
        ret = drift + vol * rng.gauss(0, 1)
        open_ = price
        price = max(0.01, price * (1 + ret))
        high = max(open_, price) * (1 + abs(rng.gauss(0, vol / 2)))
        low = min(open_, price) * (1 - abs(rng.gauss(0, vol / 2)))
        vol_ = volume_base * (1 + rng.gauss(0, 0.2))
        bars.append(
            Bar(
                date=d.isoformat(),
                open=round(open_, 4),
                high=round(high, 4),
                low=round(low, 4),
                close=round(price, 4),
                volume=round(max(0.0, vol_), 0),
            )
        )
        d += timedelta(days=1)
    return bars


def make_uptrend_bars(n: int = 300, *, seed: int = 7) -> list[Bar]:
    return make_bars(n=n, drift=0.004, vol=0.008, seed=seed)


def make_downtrend_bars(n: int = 300, *, seed: int = 11) -> list[Bar]:
    return make_bars(n=n, drift=-0.004, vol=0.008, seed=seed)


def make_flat_bars(n: int = 300, *, seed: int = 13) -> list[Bar]:
    return make_bars(n=n, drift=0.0, vol=0.002, seed=seed)


@pytest.fixture
def bars() -> list[Bar]:
    return make_bars()


@pytest.fixture
def uptrend_bars() -> list[Bar]:
    return make_uptrend_bars()


@pytest.fixture
def series(bars) -> PriceSeries:
    return PriceSeries(symbol="TEST.NS", market="IN", currency="INR", bars=bars)


@pytest.fixture
def uptrend_series(uptrend_bars) -> PriceSeries:
    return PriceSeries(symbol="UP.NS", market="IN", currency="INR", bars=uptrend_bars)


# --------------------------------------------------------------------------
# Non-contiguous trading dates (weekends skipped) -- for calendar tests
# --------------------------------------------------------------------------


def trading_days(n: int, *, start: date = date(2024, 1, 1)) -> list[str]:
    """n weekdays, skipping Saturday and Sunday."""
    out: list[str] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


# --------------------------------------------------------------------------
# Fakes that implement the real Protocols
# --------------------------------------------------------------------------


@dataclass
class FakeFeed:
    """A DataFeed that serves canned data and records what was asked for.

    `fundamental_calls` is the important field: it lets a test assert that the
    engine did NOT spend fundamentals cost (Principle P3).
    """

    series_by_symbol: dict[str, PriceSeries] = field(default_factory=dict)
    fundamentals_by_symbol: dict[str, Fundamentals] = field(default_factory=dict)
    index: str | None = "^NSEI"
    fundamental_calls: list[str] = field(default_factory=list)
    price_calls: list[str] = field(default_factory=list)
    fail_on: set[str] = field(default_factory=set)

    def price_series(self, symbol: str, days: int = 400) -> PriceSeries | None:
        self.price_calls.append(symbol)
        if symbol in self.fail_on:
            raise RuntimeError(f"fake feed failure for {symbol}")
        s = self.series_by_symbol.get(symbol)
        if s is None:
            return None
        return PriceSeries(
            symbol=s.symbol, market=s.market, currency=s.currency,
            bars=s.bars[-days:],
        )

    def fundamentals(self, symbol: str) -> Fundamentals:
        self.fundamental_calls.append(symbol)
        if symbol in self.fail_on:
            raise RuntimeError(f"fake feed failure for {symbol}")
        return self.fundamentals_by_symbol.get(
            symbol, Fundamentals(symbol=symbol, degraded=["no data"])
        )

    def index_for(self, market: str) -> str | None:
        return self.index if market == "IN" else "^GSPC"


def canned_fundamentals(
    symbol: str, *, eps_growth_pct: float = 30.0
) -> Fundamentals:
    """Fundamentals with a clean YoY acceleration, for scoring tests."""
    quarters = [
        QuarterEPS(period_end="2025-06-30", eps=2.0),
        QuarterEPS(period_end="2025-03-31", eps=1.8),
        QuarterEPS(period_end="2024-12-31", eps=1.5),
        QuarterEPS(period_end="2024-09-30", eps=1.2),
        QuarterEPS(period_end="2024-06-30", eps=1.0),
        QuarterEPS(period_end="2024-03-31", eps=0.9),
    ]
    annual = [
        QuarterEPS(period_end="2025-03-31", eps=6.0),
        QuarterEPS(period_end="2024-03-31", eps=4.0),
        QuarterEPS(period_end="2023-03-31", eps=3.0),
    ]
    return Fundamentals(
        symbol=symbol,
        quarterly_eps=quarters,
        annual_eps=annual,
        float_shares=1_000_000_000,
        shares_outstanding=1_200_000_000,
        institutional_pct=65.0,
    )


# --------------------------------------------------------------------------
# Fake strategies, one per principle they exercise
# --------------------------------------------------------------------------


class AlwaysScores:
    """Scores every name the same. Simplest possible strategy."""

    name = "always"
    version = "0.0.1"
    requires: tuple[str, ...] = ()
    needs_fundamentals = False

    def score(self, series, fundamentals, patterns=None, **options):
        return [], "B", 50

    def thesis(self, *, series, fundamentals, patterns=None,
               evidence, grade, confidence, **options):
        from trade_monitor_contracts import Invalidation, Thesis

        as_of = series.last.date if series.last else ""
        tid = hashlib.sha256(f"{series.symbol}|{as_of}".encode()).hexdigest()[:16]
        return Thesis(
            id=tid, symbol=series.symbol, name=series.symbol,
            market=series.market, currency=series.currency, as_of=as_of,
            strategy=self.name, grade=grade, confidence=confidence,
            evidence=list(evidence),
            invalidation=Invalidation(rule="test", time_stop_days=1),
        )


class NeedsFundamentals:
    """Declares needs_fundamentals=True, to test the expensive gate."""

    name = "needs_fund"
    version = "0.0.1"
    requires: tuple[str, ...] = ()
    needs_fundamentals = True

    def score(self, series, fundamentals, patterns=None, **options):
        return [], "C", 40

    def thesis(self, *, series, fundamentals, patterns=None,
               evidence, grade, confidence, **options):
        return AlwaysScores().thesis(
            series=series, fundamentals=fundamentals, patterns=patterns,
            evidence=evidence, grade=grade, confidence=confidence,
        )


class NeedsPattern:
    """Declares a pattern requirement, to test the detection gate."""

    name = "needs_pattern"
    version = "0.0.1"
    requires = ("cup_with_handle",)
    needs_fundamentals = False

    def score(self, series, fundamentals, patterns=None, **options):
        return [], "A", 90

    def thesis(self, *, series, fundamentals, patterns=None,
               evidence, grade, confidence, **options):
        return AlwaysScores().thesis(
            series=series, fundamentals=fundamentals, patterns=patterns,
            evidence=evidence, grade=grade, confidence=confidence,
        )


class Exploding:
    """Raises in score(), to test that one bad symbol does not abort a pass."""

    name = "exploding"
    version = "0.0.1"
    requires = ()
    needs_fundamentals = False

    def score(self, series, fundamentals, patterns=None, **options):
        raise ValueError("strategy exploded on purpose")

    def thesis(self, **options):
        raise ValueError("never reached")


@pytest.fixture
def fake_feed() -> FakeFeed:
    """A feed with three symbols: up, down, flat."""
    feed = FakeFeed()
    for sym, bars_ in [
        ("UP.NS", make_uptrend_bars(seed=1)),
        ("DOWN.NS", make_downtrend_bars(seed=2)),
        ("FLAT.NS", make_flat_bars(seed=3)),
    ]:
        feed.series_by_symbol[sym] = PriceSeries(
            symbol=sym, market="IN", currency="INR", bars=bars_
        )
        feed.fundamentals_by_symbol[sym] = canned_fundamentals(sym)
    feed.series_by_symbol["^NSEI"] = PriceSeries(
        symbol="^NSEI", market="IN", currency="INR", bars=make_uptrend_bars(seed=9)
    )
    return feed
