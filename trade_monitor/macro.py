"""Macro overview: market mood, yields, crypto, and cross-asset correlation.

Everything here is *computed from fetched prices*, never asserted. If a symbol
cannot be fetched the entry is marked unavailable rather than guessed, because a
market-mood panel that silently invents numbers is worse than one that admits a
gap.

Two products:

  * `overview(...)`      -- one row per instrument: last, 1d, 60d, and a mood tag
  * `correlations(...)`  -- daily log-return correlation matrix over aligned dates
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

# The instruments shown on the home page. `kind` groups them for the UI.
INSTRUMENTS: list[dict[str, str]] = [
    {"key": "sp500", "label": "S&P 500", "symbol": "^GSPC", "kind": "equity", "market": "US"},
    {"key": "nifty", "label": "Nifty 50", "symbol": "^NSEI", "kind": "equity", "market": "IN"},
    {"key": "vix", "label": "VIX", "symbol": "^VIX", "kind": "vol", "market": "US"},
    {"key": "us10y", "label": "US 10Y yield", "symbol": "^TNX", "kind": "rate", "market": "US"},
    {"key": "dxy", "label": "US Dollar", "symbol": "DX-Y.NYB", "kind": "fx", "market": "US"},
    {"key": "gold", "label": "Gold", "symbol": "GC=F", "kind": "commodity", "market": "US"},
    {"key": "oil", "label": "Crude Oil", "symbol": "CL=F", "kind": "commodity", "market": "US"},
    {"key": "btc", "label": "Bitcoin", "symbol": "BTC-USD", "kind": "crypto", "market": "US"},
]

CORR_KEYS = ["sp500", "nifty", "gold", "us10y", "btc", "dxy"]


def _pct(a: float, b: float) -> float | None:
    if b in (0, None):
        return None
    return round(100.0 * (a / b - 1.0), 2)


def _mood(kind: str, change_60d: float | None) -> str:
    """A plain-language tag. Only defined where a direction is meaningful."""
    if change_60d is None:
        return "unknown"
    if kind == "vol":
        # Rising VIX = fear; falling = calm.
        return "fear rising" if change_60d > 5 else "calm"
    if kind == "rate":
        # Rising yields tighten conditions; falling ease them.
        return "tightening" if change_60d > 5 else "easing"
    if change_60d > 2:
        return "up"
    if change_60d < -2:
        return "down"
    return "flat"


def overview(*, feed: Any, days: int = 70) -> dict[str, Any]:
    """One row per instrument. Missing instruments are marked, not hidden."""
    rows: list[dict[str, Any]] = []
    by_key: dict[str, dict[str, Any]] = {}
    for spec in INSTRUMENTS:
        row: dict[str, Any] = dict(spec)
        try:
            s = feed.price_series(spec["symbol"], days=days)
            bars = s.bars
            if len(bars) >= 2:
                last, prev, first = bars[-1].close, bars[-2].close, bars[0].close
                ch1 = _pct(last, prev)
                ch60 = _pct(last, first)
                row.update({
                    "last": round(last, 3),
                    "change_1d_pct": ch1,
                    "change_60d_pct": ch60,
                    "mood": _mood(spec["kind"], ch60),
                    "available": True,
                    "as_of": bars[-1].date,
                })
            else:
                row.update({"available": False, "reason": "insufficient bars"})
        except Exception as exc:  # noqa: BLE001 - one bad symbol is not fatal
            row.update({"available": False, "reason": f"{type(exc).__name__}"})
        rows.append(row)
        by_key[spec["key"]] = row
    return {"instruments": rows, "by_key": by_key}


def _log_returns(closes: Sequence[float]) -> list[float]:
    return [
        math.log(closes[i] / closes[i - 1])
        for i in range(1, len(closes))
        if closes[i - 1] > 0 and closes[i] > 0
    ]


def _corr(a: Sequence[float], b: Sequence[float]) -> float | None:
    n = len(a)
    if n < 10:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=False))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return None
    return round(cov / math.sqrt(va * vb), 2)


def correlations(*, feed: Any, days: int = 260) -> dict[str, Any]:
    """Daily log-return correlation over dates where every instrument has data."""
    spec_by_key = {i["key"]: i for i in INSTRUMENTS}
    closes_by: dict[str, dict[str, float]] = {}
    labels: dict[str, str] = {}
    for key in CORR_KEYS:
        spec = spec_by_key[key]
        labels[key] = spec["label"]
        try:
            s = feed.price_series(spec["symbol"], days=days)
            closes_by[key] = {b.date: b.close for b in s.bars}
        except Exception:  # noqa: BLE001
            closes_by[key] = {}

    usable = [k for k in CORR_KEYS if closes_by.get(k)]
    if len(usable) < 2:
        return {"keys": [], "labels": {}, "matrix": [], "days": 0,
                "note": "not enough instruments available"}

    common = set.intersection(*[set(closes_by[k]) for k in usable])
    dates = sorted(common)
    if len(dates) < 12:
        return {"keys": usable, "labels": labels, "matrix": [], "days": len(dates),
                "note": "not enough aligned dates"}

    rets = {
        k: _log_returns([closes_by[k][d] for d in dates])
        for k in usable
    }
    matrix: list[list[float | None]] = []
    for a in usable:
        matrix.append([_corr(rets[a], rets[b]) for b in usable])

    return {
        "keys": usable,
        "labels": {k: labels[k] for k in usable},
        "matrix": matrix,
        "days": len(dates),
        "from": dates[0],
        "to": dates[-1],
    }


# --------------------------------------------------------------------------
# Per-stock technicals for the deep-dive view
# --------------------------------------------------------------------------

def rsi(closes: Sequence[float], period: int = 14) -> float | None:
    """Wilder's RSI. Returns None if there is not enough history."""
    if len(closes) < period + 1:
        return None
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    avg_g, avg_l = gains / period, losses / period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_g = (avg_g * (period - 1) + max(d, 0)) / period
        avg_l = (avg_l * (period - 1) + max(-d, 0)) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return round(100.0 - 100.0 / (1.0 + rs), 1)


def sma(closes: Sequence[float], n: int) -> float | None:
    if len(closes) < n:
        return None
    return round(sum(closes[-n:]) / n, 3)


def technicals(bars: Sequence[Any]) -> dict[str, Any]:
    """RSI, moving averages, 52w-ish range, and support/resistance levels."""
    closes = [b.close for b in bars]
    if not closes:
        return {}
    last = closes[-1]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    r = rsi(closes)
    ma20, ma50, ma200 = sma(closes, 20), sma(closes, 50), sma(closes, 200)
    hi, lo = max(highs), min(lows)
    return {
        "last": round(last, 3),
        "rsi14": r,
        "rsi_note": _rsi_note(r),
        "ma20": ma20,
        "ma50": ma50,
        "ma200": ma200,
        "range_high": round(hi, 3),
        "range_low": round(lo, 3),
        "pct_from_high": _pct(last, hi),
        "trend": _trend(last, ma20, ma50, ma200),
    }


def _rsi_note(r: float | None) -> str:
    if r is None:
        return "unknown"
    if r >= 70:
        return "overbought"
    if r <= 30:
        return "oversold"
    return "neutral"


def _trend(last: float, ma20: float | None, ma50: float | None,
           ma200: float | None) -> str:
    above = [m for m in (ma20, ma50, ma200) if m is not None and last > m]
    total = sum(1 for m in (ma20, ma50, ma200) if m is not None)
    if total == 0:
        return "unknown"
    if len(above) == total:
        return "uptrend"
    if len(above) == 0:
        return "downtrend"
    return "mixed"


__all__ = [
    "overview", "correlations", "technicals", "rsi", "sma",
    "INSTRUMENTS", "CORR_KEYS",
]
