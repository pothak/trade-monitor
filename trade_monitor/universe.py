"""Universe definitions and tiered scanning.

Scanning all of NSE + S&P 500 is viable, but not as a single flat pass.
Yahoo rate-limits aggressive parallel fetching, so the universe is organised
into tiers that trade coverage for latency:

  tier 0  watchlist    -- explicit symbols, always scanned
  tier 1  liquid       -- index members, full fundamentals (EPS, float)
  tier 2  extended     -- full listing, price+pattern only, no fundamentals

Tier 2 is what makes full-universe coverage affordable: price data is one
request and pattern detection needs nothing else. Fundamentals (which are the
expensive part) are only fetched for names that already show a pattern.

Batching is deliberate. A full scan is ~2500 symbols; doing it as one pass
would take roughly an hour serially and would risk throttling. Workers are
configurable and requests are spaced with a jittered delay so a scan stays
inside polite request rates.
"""

from __future__ import annotations

import concurrent.futures as cf
import random
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from trade_monitor_contracts import Fundamentals, PriceSeries

# Benchmark for relative strength / market direction.
INDEX: dict[str, str] = {"IN": "^NSEI", "US": "^GSPC"}

NSE_LIST_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
SP500_LIST_URL = (
    "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/"
    "data/constituents.csv"
)


@dataclass
class Universe:
    """A named set of symbols with a market and tier."""

    name: str
    market: str
    symbols: list[str]
    tier: int = 1

    def __len__(self) -> int:
        return len(self.symbols)

    def filter(self, limit: int | None = None) -> Universe:
        if limit is None:
            return self
        return Universe(
            name=f"{self.name}[0:{limit}]",
            market=self.market,
            symbols=self.symbols[:limit],
            tier=self.tier,
        )


@dataclass
class ScanPlan:
    """Which symbols get which level of treatment this run."""

    watchlist: list[str] = field(default_factory=list)
    liquid: list[str] = field(default_factory=list)
    extended: list[str] = field(default_factory=list)
    market: str = "IN"

    @property
    def total(self) -> int:
        return len(self.watchlist) + len(self.liquid) + len(self.extended)


# --------------------------------------------------------------------------
# Symbol loading
# --------------------------------------------------------------------------


def load_nse(market: str = "IN", limit: int | None = None) -> Universe:
    """NSE listed symbols. Falls back to a curated liquid set if the
    exchange CSV is unreachable, so a scan never hard-fails on network."""
    try:
        import csv
        import io
        import urllib.request

        req = urllib.request.Request(NSE_LIST_URL, headers={"User-Agent": "canslim/0.1"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        rows = list(csv.DictReader(io.StringIO(raw)))
        symbols: list[str] = []
        for row in rows:
            sym = (row.get("Symbol") or row.get("SYMBOL") or "").strip()
            if not sym:
                continue
            symbols.append(f"{sym}.NS" if market == "IN" else sym)
        if symbols:
            return Universe(name="nse", market=market, symbols=symbols, tier=1).filter(limit)
    except Exception:  # noqa: BLE001 - degrade to fallback, never crash the scan
        pass
    return Universe(
        name="nse-fallback",
        market=market,
        symbols=[f"{s}.NS" for s in FALLBACK_NSE],
        tier=1,
    ).filter(limit)


def load_sp500(limit: int | None = None) -> Universe:
    try:
        import csv
        import io
        import urllib.request

        req = urllib.request.Request(SP500_LIST_URL, headers={"User-Agent": "canslim/0.1"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        rows = list(csv.DictReader(io.StringIO(raw)))
        symbols = [
            (r.get("Symbol") or "").strip().replace(".", "-")
            for r in rows
            if (r.get("Symbol") or "").strip()
        ]
        if symbols:
            return Universe(name="sp500", market="US", symbols=symbols, tier=1).filter(limit)
    except Exception:  # noqa: BLE001
        pass
    return Universe(name="sp500-fallback", market="US", symbols=FALLBACK_SP500, tier=1).filter(limit)


def read_symbols(path: str, market: str = "IN") -> Universe:
    """Load a watchlist from a plain-text or CSV file, one symbol per line.

    Tolerant of headers, blank lines, and a `market` column, because watchlists
    are usually exported from a spreadsheet.
    """
    import csv

    symbols: list[str] = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        if "," in sample:
            reader = csv.DictReader(fh)
            for row in reader:
                sym = (row.get("symbol") or row.get("Symbol") or row.get("code") or "").strip()
                if not sym:
                    continue
                mkt = (row.get("market") or market).strip().upper()
                symbols.append(_suffix(sym, mkt))
        else:
            for line in fh:
                sym = line.split(",")[0].split()[0].strip() if line.strip() else ""
                if sym and not sym.lower().startswith("#"):
                    symbols.append(_suffix(sym, market))
    # Preserve order, drop duplicates.
    seen: set[str] = set()
    uniq = [s for s in symbols if not (s in seen or seen.add(s))]
    return Universe(name=path.rsplit("/", 1)[-1], market=market, symbols=uniq, tier=0)


def _suffix(symbol: str, market: str) -> str:
    """Only IN and US are supported. IN names get the NSE `.NS` suffix."""
    s = symbol.strip().upper()
    if s.endswith((".NS", ".BO")):
        return s
    if market == "IN":
        return f"{s}.NS"
    return s.replace(".", "-")


def build_plan(
    *,
    markets: Sequence[str],
    watchlists: Sequence[str] = (),
    liquid_limit: int | None = None,
    extended: Sequence[str] = (),
    extended_limit: int | None = None,
) -> ScanPlan:
    """Assemble a tiered plan. Tiers are deduplicated, watchlist wins."""
    plan = ScanPlan()
    for path in watchlists:
        plan.watchlist.extend(read_symbols(path, markets[0] if markets else "IN").symbols)

    for market in markets:
        uni = load_nse(market) if market == "IN" else load_sp500()
        if liquid_limit:
            uni = uni.filter(liquid_limit)
        plan.liquid.extend(uni.symbols)
        plan.market = market

    if extended:
        for path in extended:
            u = read_symbols(path, markets[0] if markets else "IN")
            if extended_limit:
                u = u.filter(extended_limit)
            plan.extended.extend(u.symbols)

    # A symbol scanned as watchlist should not be re-scanned deeper.
    seen = set(plan.watchlist)
    plan.liquid = [s for s in dict.fromkeys(plan.liquid) if s not in seen]
    plan.extended = [s for s in dict.fromkeys(plan.extended) if s not in seen and s not in set(plan.liquid)]
    return plan


# --------------------------------------------------------------------------
# Rate-limited parallel fetch
# --------------------------------------------------------------------------


class RateLimiter:
    """Token-bucket style spacing so a scan stays polite to Yahoo."""

    def __init__(self, min_interval: float = 0.12, jitter: float = 0.08) -> None:
        self.min_interval = min_interval
        self.jitter = jitter
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if self._next > now:
                time.sleep(self._next - now)
                now = time.monotonic()
            self._next = now + self.min_interval + random.uniform(0, self.jitter)


def scan_prices(
    symbols: Sequence[str],
    fetch: Callable[[str], PriceSeries],
    *,
    workers: int = 8,
    limiter: RateLimiter | None = None,
    on_error: Callable[[str, Exception], None] | None = None,
) -> dict[str, PriceSeries]:
    """Fetch price series for many symbols concurrently.

    A failed symbol is dropped, never fatal: yfinance 404s on delisted and
    suspended names, which is expected when scanning a full listing.
    """
    out: dict[str, PriceSeries] = {}
    if not symbols:
        return out

    def guarded(sym: str) -> tuple[str, PriceSeries | None]:
        if limiter:
            limiter.wait()
        try:
            return sym, fetch(sym)
        except Exception:  # noqa: BLE001
            return sym, None

    with cf.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for sym, series in pool.map(guarded, symbols):
            if series is not None and len(series) >= 60:
                out[sym] = series
            elif on_error:
                on_error(sym, LookupError("no usable price series"))
    return out


def scan_fundamentals(
    symbols: Sequence[str],
    fetch: Callable[[str], Fundamentals],
    *,
    workers: int = 4,
    limiter: RateLimiter | None = None,
) -> dict[str, Fundamentals]:
    """Fundamentals are heavier and more rate-limited, so fewer workers."""
    out: dict[str, Fundamentals] = {}
    if not symbols:
        return out

    def guarded(sym: str) -> tuple[str, Fundamentals | None]:
        if limiter:
            limiter.wait()
        try:
            return sym, fetch(sym)
        except Exception:  # noqa: BLE001
            return sym, None

    with cf.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for sym, fund in pool.map(guarded, symbols):
            if fund is not None:
                out[sym] = fund
    return out


# --------------------------------------------------------------------------
# Fallback liquid sets (used when exchange listings are unreachable)
# --------------------------------------------------------------------------

FALLBACK_NSE: tuple[str, ...] = (
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "ITC", "SBIN", "BHARTIARTL",
    "LT", "AXISBANK", "KOTAKBANK", "HINDUNILVR", "BAJFINANCE", "MARUTI", "SUNPHARMA",
    "TITAN", "ULTRACEMCO", "ASIANPAINT", "BAJAJFINSV", "ADANIENT", "ADANIPORTS",
    "POWERGRID", "NTPC", "ONGC", "COALINDIA", "JSWSTEEL", "TATASTEEL", "WIPRO",
    "HCLTECH", "TECHM", "NESTLEIND", "TATAMOTORS", "M&M", "ADANIPOWER", "SBILIFE",
    "BRITANNIA", "CIPLA", "DRREDDY", "GRASIM", "HINDALCO", "INDUSINDBK", "EICHERMOT",
    "BAJAJ-AUTO", "HEROMOTOCO", "DIVISLAB", "APOLLOHOSP", "TATACONSUM", "BPCL",
)

FALLBACK_SP500: tuple[str, ...] = (
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "BRK-B", "LLY",
    "JPM", "V", "XOM", "UNH", "MA", "PG", "JNJ", "HD", "COST", "ABBV",
    "WMT", "NFLX", "AMD", "CRM", "KO", "PEP", "ADBE", "MRK", "CVX", "BAC",
    "ORCL", "CSCO", "ACN", "MCD", "LIN", "TMO", "DIS", "ABT", "TXN", "PFE",
    "CAT", "VZ", "CMCSA", "NKE", "WFC", "COP", "AMGN", "PM", "IBM", "GS",
)
