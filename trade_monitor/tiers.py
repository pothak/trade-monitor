"""Size / liquidity tier (UI-3).

An honest label for how easy a name is to actually trade. Two independent axes:

  * **size**      -- market cap band, from price x shares outstanding
  * **liquidity** -- recent rupee/dollar turnover band, from price x volume

Both are surfaced so the UI can warn when a thesis is on a name too small or
too thin to fill. The tier is *derived from what we actually have*: when shares
outstanding is missing (common for `.NS` via free data) size is reported as
`unknown` rather than guessed. Missing data must never masquerade as "large".
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from trade_monitor_contracts import Fundamentals

# Thresholds in local currency. India and the US differ by ~80x in FX, so a
# single number would mislabel every Indian mid-cap as a micro-cap.
_SIZE_BANDS_INR = [(200_000_000_000, "large"), (20_000_000_000, "mid"), (2_000_000_000, "small")]
_SIZE_BANDS_USD = [(10_000_000_000, "large"), (2_000_000_000, "mid"), (300_000_000, "small")]
# Median daily turnover of the last N sessions, in local currency.
_LIQ_BANDS_INR = [(500_000_000, "high"), (50_000_000, "medium"), (5_000_000, "low")]
_LIQ_BANDS_USD = [(20_000_000, "high"), (2_000_000, "medium"), (200_000, "low")]


def _band(value: float | None, bands: Sequence[tuple[float, str]]) -> str:
    if value is None or value <= 0:
        return "unknown"
    for floor, label in bands:
        if value >= floor:
            return label
    return "micro"


def market_cap(price: float | None, fund: Fundamentals | None) -> float | None:
    if fund is None or price is None:
        return None
    shares = fund.shares_outstanding or fund.float_shares
    if not shares or price <= 0:
        return None
    return price * shares


def median_turnover(bars: Sequence[Any], lookback: int = 20) -> float | None:
    """Median of close x volume over the most recent `lookback` bars."""
    rows = bars[-lookback:] if lookback else bars
    turns: list[float] = []
    for b in rows:
        if b.close > 0 and b.volume > 0:
            turns.append(b.close * b.volume)
    if not turns:
        return None
    turns.sort()
    mid = len(turns) // 2
    if len(turns) % 2:
        return turns[mid]
    return (turns[mid - 1] + turns[mid]) / 2.0


def size_tier(
    *,
    market: str,
    price: float | None,
    fund: Fundamentals | None,
    bars: Sequence[Any],
) -> dict[str, Any]:
    """Return {size, liquidity, market_cap, turnover} for one name."""
    cap = market_cap(price, fund)
    turnover = median_turnover(bars)
    inr = market.upper() == "IN"
    size = _band(cap, _SIZE_BANDS_INR if inr else _SIZE_BANDS_USD)
    liq = _band(turnover, _LIQ_BANDS_INR if inr else _LIQ_BANDS_USD)
    return {
        "size": size,
        "liquidity": liq,
        "market_cap": round(cap, 2) if cap is not None else None,
        "turnover": round(turnover, 2) if turnover is not None else None,
    }


def warnings(tier: dict[str, Any]) -> list[str]:
    """Human-readable cautions implied by a tier, for the UI to surface."""
    out: list[str] = []
    if tier.get("size") in ("small", "micro"):
        out.append(f"small cap ({tier['size']})")
    if tier.get("liquidity") == "low":
        out.append("thin trading — check you can fill")
    if tier.get("size") == "unknown" and tier.get("liquidity") == "unknown":
        out.append("size and liquidity unknown (no data)")
    return out


__all__ = ["size_tier", "market_cap", "median_turnover", "warnings"]
