"""On-disk cache for fetched data (DATA-8).

Re-fetching the same price series or fundamentals within a TTL is wasteful and
rate-limit-prone. This caches results as JSON under ~/.trade-monitor/cache/,
keyed by a hash of (provider, kind, symbol, params).

Two properties matter:

  * **never fatal** -- a corrupt or missing cache entry is a miss, not an error
  * **TTL-bounded** -- a cached value older than its TTL is ignored, so a stale
    quote can never masquerade as current

Data classes are cached via their `dataclasses.asdict` form and rebuilt with
the same constructor, so the cache stores plain JSON and no pickle is involved.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any


def default_cache_dir() -> Path:
    env = os.environ.get("TRADE_MONITOR_CACHE")
    if env:
        return Path(env).expanduser()
    return Path("~/.trade-monitor/cache").expanduser()


class DiskCache:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root).expanduser() if root else default_cache_dir()

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return self.root / digest[:2] / f"{digest}.json"

    def get(self, key: str, ttl: float) -> Any | None:
        p = self._path(key)
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            return None
        if time.time() - float(raw.get("at", 0)) > ttl:
            return None
        return raw.get("value")

    def put(self, key: str, value: Any) -> None:
        p = self._path(key)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(
                json.dumps({"at": time.time(), "value": value}), encoding="utf-8"
            )
            tmp.replace(p)  # atomic; a concurrent reader never sees a partial file
        except OSError:
            pass  # a cache write failure must never break a scan

    def clear(self) -> int:
        n = 0
        for p in self.root.rglob("*.json"):
            try:
                p.unlink()
                n += 1
            except OSError:
                pass
        return n

    def size(self) -> int:
        return sum(1 for _ in self.root.rglob("*.json"))


# --------------------------------------------------------------------------
# (de)serialization for the contract dataclasses we cache
# --------------------------------------------------------------------------


def series_to_json(series: Any) -> dict[str, Any]:
    return {
        "symbol": series.symbol, "market": series.market,
        "currency": series.currency,
        "bars": [
            {"date": b.date, "open": b.open, "high": b.high,
             "low": b.low, "close": b.close, "volume": b.volume}
            for b in series.bars
        ],
    }


def series_from_json(data: dict[str, Any]) -> Any:
    from trade_monitor_contracts import Bar, PriceSeries

    return PriceSeries(
        symbol=data["symbol"], market=data["market"], currency=data["currency"],
        bars=[Bar(**b) for b in data["bars"]],
    )


def fundamentals_to_json(f: Any) -> dict[str, Any]:
    return {
        "symbol": f.symbol,
        "quarterly_eps": [{"period_end": q.period_end, "eps": q.eps,
                           "reported": q.reported} for q in f.quarterly_eps],
        "annual_eps": [{"period_end": q.period_end, "eps": q.eps,
                        "reported": q.reported} for q in f.annual_eps],
        "float_shares": f.float_shares,
        "shares_outstanding": f.shares_outstanding,
        "institutional_pct": f.institutional_pct,
        "institutional_detail": f.institutional_detail,
        "degraded": f.degraded,
    }


def fundamentals_from_json(data: dict[str, Any]) -> Any:
    from trade_monitor_contracts import Fundamentals, QuarterEPS

    return Fundamentals(
        symbol=data["symbol"],
        quarterly_eps=[QuarterEPS(**q) for q in data.get("quarterly_eps", [])],
        annual_eps=[QuarterEPS(**q) for q in data.get("annual_eps", [])],
        float_shares=data.get("float_shares"),
        shares_outstanding=data.get("shares_outstanding"),
        institutional_pct=data.get("institutional_pct"),
        institutional_detail=data.get("institutional_detail", []),
        degraded=data.get("degraded", []),
    )


def cached_call(
    cache: DiskCache | None,
    key: str,
    ttl: float,
    produce: Callable[[], Any],
    *,
    to_json: Callable[[Any], Any],
    from_json: Callable[[Any], Any],
) -> Any:
    """Return a cached value, or call `produce`, cache and return it."""
    if cache is not None:
        hit = cache.get(key, ttl)
        if hit is not None:
            try:
                return from_json(hit)
            except (KeyError, TypeError, ValueError):
                pass  # corrupt entry -> treat as a miss
    value = produce()
    if cache is not None:
        try:
            cache.put(key, to_json(value))
        except (TypeError, ValueError):
            pass
    return value


__all__ = [
    "DiskCache", "cached_call", "default_cache_dir",
    "series_to_json", "series_from_json",
    "fundamentals_to_json", "fundamentals_from_json",
]
