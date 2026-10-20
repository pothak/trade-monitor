"""yfinance data feed.

The only provider implemented today. Registered by name so config can select a
different feed later without the engine changing.

Known coverage gaps, surfaced as `degraded` rather than silently defaulted:
  - institutional holders: US only (yfinance returns empty for .NS/.BO)
  - Indian quarterly EPS series have holes; do not assume contiguity
"""

from __future__ import annotations

import time
from typing import Any

from trade_monitor_contracts import Bar, Fundamentals, Market, PriceSeries, QuarterEPS
from ..registry import Registry

MARKETS: dict[str, str] = {"IN": "^NSEI", "US": "^GSPC"}

_INDEX_OVERRIDE: dict[str, str] = {
    "RELIANCE.NS": "NIFTY 50",
    "TCS.NS": "NIFTY 50",
    "INFY.NS": "NIFTY 50",
    "HDFCBANK.NS": "NIFTY 50",
}


class YFinanceFeed:
    name = "yfinance"

    def __init__(self, *, cache_ttl_seconds: int = 900, retries: int = 3) -> None:
        self._cache_ttl = cache_ttl_seconds
        self._retries = retries
        self._price_cache: dict[tuple[str, int], tuple[float, PriceSeries]] = {}
        self._fund_cache: dict[str, tuple[float, Fundamentals]] = {}

    # -- helpers ---------------------------------------------------------

    def _cached(self, store: dict, key: Any):
        hit = store.get(key)
        if hit and (time.time() - hit[0]) < self._cache_ttl:
            return hit[1]
        return None

    def _store(self, store: dict, key: Any, value: Any) -> None:
        store[key] = (time.time(), value)

    @staticmethod
    def market_of(symbol: str) -> Market:
        s = symbol.upper()
        if s.endswith((".NS", ".BO")):
            return "IN"
        return "US"

    @staticmethod
    def currency_of(symbol: str) -> str:
        return "INR" if symbol.upper().endswith((".NS", ".BO")) else "USD"

    def index_for(self, market: Market) -> str | None:
        return MARKETS.get(market)

    def _ticker(self, symbol: str):
        import yfinance as yf

        return yf.Ticker(symbol)

    # -- prices ----------------------------------------------------------

    def price_series(self, symbol: str, days: int = 400) -> PriceSeries | None:
        key = (symbol, days)
        hit = self._cached(self._price_cache, key)
        if hit:
            return hit

        # yfinance day counts are calendar-ish; oversample so we retain enough
        # sessions after holidays/suspensions.
        span = max(int(days * 1.6), 60)
        last_err: Exception | None = None
        for attempt in range(self._retries):
            try:
                hist = self._ticker(symbol).history(period=f"{span}d", auto_adjust=False)
                if hist is None or hist.empty:
                    raise ValueError("empty frame")
                break
            except Exception as exc:  # noqa: BLE001 - surfaced after retries
                last_err = exc
                hist = None
                time.sleep(0.8 * (attempt + 1))
        if hist is None or getattr(hist, "empty", True):
            raise LookupError(f"yfinance: no price series for {symbol}: {last_err}")

        bars = [
            Bar(
                date=idx.strftime("%Y-%m-%d"),
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=float(row.get("Volume", 0) or 0),
            )
            for idx, row in hist.iterrows()
            if row["Close"] == row["Close"]  # drop NaN closes
        ]
        if not bars:
            raise LookupError(f"yfinance: all bars NaN for {symbol}")

        series = PriceSeries(
            symbol=symbol,
            market=self.market_of(symbol),
            currency=self.currency_of(symbol),
            bars=bars[-days:],
        )
        self._store(self._price_cache, key, series)
        return series

    # -- fundamentals ----------------------------------------------------

    def fundamentals(self, symbol: str) -> Fundamentals:
        hit = self._cached(self._fund_cache, symbol)
        if hit:
            return hit

        degraded: list[str] = []
        ticker = self._ticker(symbol)
        market = self.market_of(symbol)

        quarterly = self._extract_eps(ticker.quarterly_income_stmt, "quarter")
        annual = self._extract_eps(ticker.income_stmt, "annual")

        if quarterly is None:
            degraded.append("quarterly EPS unavailable")
        if annual is None:
            degraded.append("annual EPS unavailable")
        if quarterly and len(quarterly) < 5:
            degraded.append(
                f"quarterly EPS sparse ({len(quarterly)} quarters reported) "
                "- gaps present, growth computed YoY on matched quarters only"
            )

        info: dict[str, Any] = {}
        try:
            info = ticker.info or {}
        except Exception:  # noqa: BLE001
            degraded.append("ticker.info unavailable (float/institutional missing)")

        float_shares = info.get("floatShares")
        shares_out = info.get("sharesOutstanding")

        inst_detail: list[dict[str, Any]] = []
        inst_pct: float | None = None
        try:
            inst = ticker.institutional_holders
            if inst is not None and len(inst):
                inst_detail = inst.head(20).reset_index().to_dict("records")
                inst_pct = float(inst["pctHeld"].sum() * 100)
        except Exception:  # noqa: BLE001
            pass

        if inst_pct is None:
            if market == "IN":
                degraded.append(
                    "institutional sponsorship (I) unavailable for IN "
                    "- yfinance has no India holder data"
                )
            else:
                degraded.append("institutional sponsorship (I) unavailable")

        fund = Fundamentals(
            symbol=symbol,
            quarterly_eps=quarterly or [],
            annual_eps=annual or [],
            float_shares=float(float_shares) if float_shares else None,
            shares_outstanding=float(shares_out) if shares_out else None,
            institutional_pct=inst_pct,
            institutional_detail=inst_detail,
            degraded=degraded,
        )
        self._store(self._fund_cache, symbol, fund)
        return fund

    @staticmethod
    def _extract_eps(frame, kind: str) -> list[QuarterEPS] | None:
        """Pull EPS rows out of a yfinance statement frame.

        Indian quarterly EPS is reported per-quarter but the series is often
        non-contiguous, so we keep only periods with a real value and let the
        scorer match same-quarter-year-over-year rather than assume adjacency.
        """
        if frame is None or getattr(frame, "empty", True):
            return None

        row = None
        for label in ("Diluted EPS", "Basic EPS"):
            if label in frame.index:
                row = frame.loc[label]
                break
        if row is None:
            return None

        out: list[QuarterEPS] = []
        for col, value in row.items():
            try:
                v = float(value)
            except (TypeError, ValueError):
                continue
            if v != v:  # NaN
                continue
            out.append(QuarterEPS(period_end=col.strftime("%Y-%m-%d"), eps=v))
        out.sort(key=lambda q: q.period_end, reverse=True)
        if kind == "annual":
            # De-dup: yfinance sometimes repeats a period across columns.
            seen: set[str] = set()
            deduped: list[QuarterEPS] = []
            for q in out:
                if q.period_end in seen:
                    continue
                seen.add(q.period_end)
                deduped.append(q)
            out = deduped
        return out


REGISTRY: Registry[YFinanceFeed] = Registry()
REGISTRY.kind = "datafeed"
REGISTRY.register(
    "yfinance",
    YFinanceFeed,
    description="Yahoo Finance via yfinance. Free, no key. IN+US. "
    "No institutional holder data for India.",
    version="0.1.0",
    provides=("price", "eps_quarterly", "eps_annual", "float"),
    config_schema={
        "cache_ttl_seconds": {"type": "integer", "default": 900},
        "retries": {"type": "integer", "default": 3},
    },
)