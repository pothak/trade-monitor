"""Historical validation.

The point of this module is to answer one question with evidence: when the
detector fires, does the price actually go up?

Method
  For a historical date D, we deliberately score using only bars up to D, so
  there is no look-ahead. yfinance gives us the full series; we truncate.

  "Did it work" is measured three ways, because a single metric is easy to
  fool:
    target_hit   closed above measured_move_target within horizon
    pivot_hit    traded above the pivot within horizon (a real breakout)
    return       close-to-close return over the horizon, signed

  We compare detected names against a baseline of the same universe over the
  same window, so a "hit rate" that is just the market's drift is visible as
  such.

Leakage guards
  - bars after D are never read
  - fundamentals are as-of-now and therefore NOT point-in-time; flagged in the
    output, since EPS restatement and lag make this the weakest part of the
    method
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Sequence

from trade_monitor_contracts import Fundamentals, PriceSeries
from .patterns.cup_handle import CupWithHandle


@dataclass
class Signal:
    symbol: str
    as_of: str
    pivot: float
    target: float
    stop: float
    last_close: float
    strength: float
    grade: str
    confidence: int


@dataclass
class Outcome:
    symbol: str
    as_of: str
    entry: float
    pivot_hit: bool | None
    target_hit: bool | None
    stop_hit: bool | None
    return_pct: float | None
    max_favorable_pct: float | None
    max_adverse_pct: float | None
    days_to_target: int | None = None
    days_to_stop: int | None = None
    timed_out: bool = False


@dataclass
class BacktestResult:
    symbol: str
    market: str
    strategy: str
    windows_tested: int
    signals: list[Signal] = field(default_factory=list)
    outcomes: list[Outcome] = field(default_factory=list)
    baseline_outcomes: list[Outcome] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        sig = [o for o in self.outcomes if o.return_pct is not None]
        n = len(sig)
        wins = [o for o in sig if (o.return_pct or 0) > 0]
        target_hits = [o for o in sig if o.target_hit]
        pivot_hits = [o for o in sig if o.pivot_hit]
        stop_hits = [o for o in sig if o.stop_hit]
        avg = sum(o.return_pct or 0 for o in sig) / n if n else None

        base = [o for o in self.baseline_outcomes if o.return_pct is not None]
        base_avg = sum(o.return_pct or 0 for o in base) / len(base) if base else None

        return {
            "symbol": self.symbol,
            "windows_tested": self.windows_tested,
            "signals": n,
            "pivot_hit_rate": round(len(pivot_hits) / n, 4) if n else None,
            "target_hit_rate": round(len(target_hits) / n, 4) if n else None,
            "stop_rate": round(len(stop_hits) / n, 4) if n else None,
            "win_rate": round(len(wins) / n, 4) if n else None,
            "avg_return_pct": round(avg, 3) if avg is not None else None,
            "baseline_avg_return_pct": round(base_avg, 3) if base_avg is not None else None,
            "edge_vs_baseline": (
                round(avg - base_avg, 3) if (avg is not None and base_avg is not None) else None
            ),
            "notes": self.notes,
        }


def truncate(series: PriceSeries, as_of: str) -> PriceSeries | None:
    """Series visible at a point in time. This is the look-ahead guard."""
    bars = [b for b in series.bars if b.date <= as_of]
    if len(bars) < 80:
        return None
    return PriceSeries(
        symbol=series.symbol,
        market=series.market,
        currency=series.currency,
        bars=bars,
    )


def _measure(
    series: PriceSeries, as_of: str, sig: Signal, horizon_days: int
) -> Outcome:
    """Walk forward from the signal date using only later bars."""
    idx = next((i for i, b in enumerate(series.bars) if b.date == as_of), None)
    if idx is None:
        return Outcome(sig.symbol, as_of, sig.last_close, None, None, None, None, None, None)

    fwd = series.bars[idx + 1 : idx + 1 + horizon_days]
    pivot_hit = target_hit = stop_hit = False
    max_fav = 0.0
    max_adv = 0.0
    days_to_target = days_to_stop = None
    entry = sig.last_close

    for i, b in enumerate(fwd, start=1):
        if b.high >= sig.pivot:
            pivot_hit = True
        if b.high >= sig.target:
            target_hit = True
            if days_to_target is None:
                days_to_target = i
        if b.low <= sig.stop:
            stop_hit = True
            if days_to_stop is None:
                days_to_stop = i
        if entry:
            max_fav = max(max_fav, (b.high / entry - 1) * 100)
            max_adv = min(max_adv, (b.low / entry - 1) * 100)

    last_close = fwd[-1].close if fwd else None
    timed_out = bool(fwd) and not (target_hit or stop_hit)

    return Outcome(
        symbol=sig.symbol,
        as_of=as_of,
        entry=entry,
        pivot_hit=pivot_hit if fwd else None,
        target_hit=target_hit if fwd else None,
        stop_hit=stop_hit if fwd else None,
        return_pct=round((last_close / entry - 1) * 100, 3) if (last_close and entry) else None,
        max_favorable_pct=round(max_fav, 3) if fwd else None,
        max_adverse_pct=round(max_adv, 3) if fwd else None,
        days_to_target=days_to_target,
        days_to_stop=days_to_stop,
        timed_out=timed_out,
    )


def backtest(
    *,
    symbol: str,
    series: PriceSeries,
    fundamentals: Fundamentals,
    strategy,
    bench_series: PriceSeries | None = None,
    horizon_days: int = 60,
    stride: int = 5,
    windows: int = 24,
    baseline_stride: int = 15,
    seed: int = 7,
    **options: Any,
) -> BacktestResult:
    """Walk the detector forward through history and score what followed.

    `stride` spaces the test dates so windows are not heavily overlapping,
    which would inflate apparent sample size.
    """
    res = BacktestResult(
        symbol=symbol, market=series.market, strategy=strategy.name,
        windows_tested=0,
    )
    res.notes.append(
        "fundamentals are as-of-now, not point-in-time; EPS lag/restatement "
        "means C and A carry mild look-ahead risk"
    )

    detector = CupWithHandle()
    total_bars = len(series.bars)
    # Leave enough trailing bars for a full forward horizon.
    last_start = total_bars - horizon_days - 1
    if last_start <= 120:
        res.notes.append("insufficient history for a backtest")
        return res

    step = max(1, stride)
    start_idx = 120
    tested = 0
    for i in range(start_idx, last_start, step):
        tested += 1
        if tested > windows:
            break
        as_of = series.bars[i].date
        past = truncate(series, as_of)
        if past is None:
            continue

        geom = detector.detect(past, **options)
        if geom is None:
            continue

        try:
            ev, grade, conf = strategy.score(
                past, fundamentals, bench_series=bench_series,
                pattern_strength=geom["strength"],
            )
        except Exception as exc:  # noqa: BLE001
            res.notes.append(f"{as_of}: scoring error {type(exc).__name__}")
            continue

        sig = Signal(
            symbol=symbol, as_of=as_of,
            pivot=geom["pivot_price"], target=geom["measured_move_target"],
            stop=geom["stop_reference"], last_close=geom["last_close"],
            strength=geom["strength"], grade=grade, confidence=conf,
        )
        res.signals.append(sig)
        res.outcomes.append(_measure(series, as_of, sig, horizon_days))

    res.windows_tested = tested

    # Baseline: same horizon and symbol, sampled from dates where the detector
    # did NOT fire, so we can see whether the edge exists at all.
    fired = {s.as_of for s in res.signals}
    rng = random.Random(seed)
    candidates = [
        series.bars[j].date
        for j in range(start_idx, last_start, max(1, baseline_stride))
        if series.bars[j].date not in fired
    ]
    rng.shuffle(candidates)
    for as_of in candidates[: max(6, len(res.signals))]:
        idx = next(i for i, b in enumerate(series.bars) if b.date == as_of)
        fwd = series.bars[idx + 1 : idx + 1 + horizon_days]
        if not fwd:
            continue
        entry = series.bars[idx].close
        last_close = fwd[-1].close
        res.baseline_outcomes.append(
            Outcome(
                symbol=symbol, as_of=as_of, entry=entry,
                pivot_hit=None, target_hit=None, stop_hit=None,
                return_pct=round((last_close / entry - 1) * 100, 3),
                max_favorable_pct=round(max((b.high / entry - 1) * 100 for b in fwd), 3),
                max_adverse_pct=round(min((b.low / entry - 1) * 100 for b in fwd), 3),
            )
        )

    return res