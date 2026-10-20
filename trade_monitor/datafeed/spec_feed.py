"""Declarative data feeds.

A feed can be defined without writing code. A `.toml` file describes an HTTP
endpoint and how its JSON maps to our `Bar` shape; this module interprets it.
Nothing here uses `exec` -- a spec can only make the requests it declares.

Why this exists: "create a new feed source" is a first-class need, but letting
a UI upload executable code is a large surface. A declarative spec covers the
common case (a REST/CSV endpoint that returns candles) with no code execution
and keeps credentials out of the file (named by `auth_env`, read from the
environment at call time).

Spec shape (all keys except name/kind/url are optional):

    [feed]
    name = "mydata"
    kind = "rest"
    description = "..."
    enabled = true

    [feed.http]
    url = "https://api.example.com/quote/{symbol}"
    method = "GET"
    auth_header = "Authorization"     # optional
    auth_env = "MYDATA_TOKEN"         # env var holding the token
    query = { interval = "1d" }       # optional, appended as query params
    timeout = 10

    [feed.map]
    bars   = "$.candles"              # path to the list of bars
    date   = "t"                      # keys within each bar item
    open   = "o"
    high   = "h"
    low    = "l"
    close  = "c"
    volume = "v"
    date_format = "epoch"             # "iso" (default) or "epoch" (unix seconds)

    [feed.markets]
    IN = "^NSEI"
    US = "^GSPC"

A `path` expression is dot/index based, e.g. `$.data.candles` or `$.results.0`.
The generic driver is registered under the feed kind as `"rest"`, so the engine
can use a spec-defined feed exactly like the built-in yfinance one.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trade_monitor_contracts import Bar, Fundamentals, Market, PriceSeries

try:  # Python 3.11+ ships tomllib; 3.10 needs tomli
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


class FeedSpecError(ValueError):
    """A spec is malformed. Surfaced as plugin status=error, never fatal."""


# --------------------------------------------------------------------------
# Minimal JSON path
# --------------------------------------------------------------------------


def json_path(obj: Any, path: str) -> Any:
    """Resolve a `$.a.b.0` style path against parsed JSON.

    Deliberately tiny: dots split keys, numeric segments index lists. It is not
    a full JSONPath implementation and does not need to be -- a spec author
    maps one flat structure, not a query.
    """
    if path in ("", "$", None):
        return obj
    expr = path[2:] if path.startswith("$.") else path.lstrip("$").lstrip(".")
    cur = obj
    for seg in expr.split("."):
        if seg == "":
            continue
        if isinstance(cur, list):
            try:
                cur = cur[int(seg)]
            except (ValueError, IndexError) as exc:
                raise FeedSpecError(f"path {path!r}: bad index {seg!r}") from exc
        elif isinstance(cur, dict):
            if seg not in cur:
                raise FeedSpecError(f"path {path!r}: key {seg!r} missing")
            cur = cur[seg]
        else:
            raise FeedSpecError(f"path {path!r}: cannot descend into {type(cur).__name__}")
    return cur


# --------------------------------------------------------------------------
# Spec model
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FeedSpec:
    name: str
    kind: str = "rest"
    description: str = ""
    enabled: bool = True
    version: str = "0.1.0"

    url: str = ""
    method: str = "GET"
    auth_header: str | None = None
    auth_env: str | None = None
    query: dict[str, Any] = field(default_factory=dict)
    timeout: float = 10.0
    headers: dict[str, str] = field(default_factory=dict)

    field_map: dict[str, str] = field(default_factory=dict)
    date_format: str = "iso"
    markets: dict[str, str] = field(default_factory=dict)
    source_path: str = ""

    @property
    def required_map_keys(self) -> tuple[str, ...]:
        common = ("date", "open", "high", "low", "close")
        if self.field_map.get("shape") == "columns":
            return common
        return ("bars",) + common

    def validate(self) -> None:
        if not self.name:
            raise FeedSpecError("feed.name is required")
        if not self.url:
            raise FeedSpecError("feed.http.url is required")
        missing = [k for k in self.required_map_keys if k not in self.field_map]
        if missing:
            raise FeedSpecError(f"feed.map missing required keys: {missing}")
        if self.date_format not in ("iso", "epoch"):
            raise FeedSpecError("feed.map.date_format must be 'iso' or 'epoch'")
        shape = self.field_map.get("shape", "rows")
        if shape not in ("rows", "columns"):
            raise FeedSpecError("feed.map.shape must be 'rows' or 'columns'")

    def to_manifest(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": "feed",
            "origin": "spec",
            "version": self.version,
            "enabled": self.enabled,
            "status": "disabled" if not self.enabled else "ok",
            "description": self.description,
            "params": {
                "url": self.url,
                "method": self.method,
                "auth_env": self.auth_env,
                "markets": self.markets,
            },
            "config_schema": {},
            "source": self.source_path,
        }


def parse_spec(text: str, *, source: str = "") -> FeedSpec:
    """Parse TOML into a FeedSpec. Raises FeedSpecError on anything invalid."""
    try:
        raw = tomllib.loads(text)
    except Exception as exc:  # noqa: BLE001
        raise FeedSpecError(f"invalid TOML: {exc}") from exc

    feed = raw.get("feed")
    if not isinstance(feed, dict):
        raise FeedSpecError("missing [feed] table")

    http = feed.get("http", {}) or {}
    mapping = feed.get("map", {}) or {}
    if not isinstance(http, dict) or not isinstance(mapping, dict):
        raise FeedSpecError("[feed.http] and [feed.map] must be tables")

    spec = FeedSpec(
        name=str(feed.get("name", "")).strip(),
        kind=str(feed.get("kind", "rest")).strip() or "rest",
        description=str(feed.get("description", "")),
        enabled=bool(feed.get("enabled", True)),
        version=str(feed.get("version", "0.1.0")),
        url=str(http.get("url", "")).strip(),
        method=str(http.get("method", "GET")).upper(),
        auth_header=http.get("auth_header"),
        auth_env=http.get("auth_env"),
        query=dict(http.get("query", {}) or {}),
        timeout=float(http.get("timeout", 10.0)),
        headers={str(k): str(v) for k, v in (http.get("headers", {}) or {}).items()},
        field_map={str(k): str(v) for k, v in mapping.items() if k != "date_format"},
        date_format=str(mapping.get("date_format", "iso")),
        markets={str(k): str(v) for k, v in (feed.get("markets", {}) or {}).items()},
        source_path=source,
    )
    spec.validate()
    return spec


def load_spec_file(path: str | Path) -> FeedSpec:
    p = Path(path)
    return parse_spec(p.read_text(encoding="utf-8"), source=str(p))


# --------------------------------------------------------------------------
# The generic driver
# --------------------------------------------------------------------------


def _to_iso(value: Any, date_format: str) -> str:
    if date_format == "epoch":
        ts = float(value)
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    s = str(value)
    # Accept a couple of common ISO-ish layouts and normalize to date only.
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[: len(fmt) + 2], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except ValueError:
        return s[:10]


class SpecFeed:
    """A DataFeed built from a FeedSpec. Registry kind `"rest"`."""

    def __init__(
        self,
        spec: FeedSpec,
        *,
        opener: Callable[[urllib.request.Request, float], bytes] | None = None,
        cache_ttl_seconds: int = 900,
    ) -> None:
        self.spec = spec
        self.name = spec.name
        self._opener = opener or _default_opener
        self._cache_ttl = cache_ttl_seconds
        self._price_cache: dict[tuple[str, int], tuple[float, PriceSeries]] = {}

    # -- DataFeed protocol ----------------------------------------------

    def index_for(self, market: Market) -> str | None:
        return self.spec.markets.get(market)

    def price_series(self, symbol: str, days: int = 400) -> PriceSeries | None:
        import time

        key = (symbol, days)
        hit = self._price_cache.get(key)
        if hit and (time.time() - hit[0]) < self._cache_ttl:
            return hit[1]

        payload = self._fetch(symbol)
        bars = self._map_bars(payload)[-days:]
        if not bars:
            raise LookupError(f"spec feed {self.name}: no bars for {symbol}")
        series = PriceSeries(
            symbol=symbol,
            market=self._market_of(symbol),
            currency=self._currency_of(symbol),
            bars=bars,
        )
        self._price_cache[key] = (time.time(), series)
        return series

    def fundamentals(self, symbol: str) -> Fundamentals:
        # A price-only spec feed is honest about it rather than inventing data.
        return Fundamentals(
            symbol=symbol,
            degraded=[f"spec feed {self.name!r} provides prices only"],
        )

    # -- internals -------------------------------------------------------

    def _market_of(self, symbol: str) -> Market:
        s = symbol.upper()
        if s.endswith((".NS", ".BO")):
            return "IN"
        # Fall back to whichever markets the spec declares, if only one.
        if set(self.spec.markets) == {"IN"}:
            return "IN"
        if set(self.spec.markets) == {"US"}:
            return "US"
        return "US"

    @staticmethod
    def _currency_of(symbol: str) -> str:
        return "INR" if symbol.upper().endswith((".NS", ".BO")) else "USD"

    def _fetch(self, symbol: str) -> Any:
        from urllib.parse import quote, urlencode

        spec = self.spec
        url = spec.url.replace("{symbol}", quote(symbol, safe=""))
        if spec.query:
            sep = "&" if "?" in url else "?"
            url = url + sep + urlencode(spec.query)

        headers = dict(spec.headers)
        if spec.auth_header and spec.auth_env:
            token = os.environ.get(spec.auth_env)
            if not token:
                raise FeedSpecError(
                    f"feed {spec.name}: env var {spec.auth_env!r} is not set"
                )
            headers[spec.auth_header] = token

        req = urllib.request.Request(url, headers=headers, method=spec.method)
        raw = self._opener(req, spec.timeout)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FeedSpecError(f"feed {spec.name}: response is not JSON: {exc}") from exc

    def _map_bars(self, payload: Any) -> list[Bar]:
        m = self.spec.field_map
        if m.get("shape") == "columns":
            return self._map_columns(payload)
        rows = json_path(payload, m["bars"])
        if not isinstance(rows, list):
            raise FeedSpecError(
                f"feed.map.bars path {m['bars']!r} did not resolve to a list"
            )
        out: list[Bar] = []
        for item in rows:
            try:
                out.append(
                    Bar(
                        date=_to_iso(_rel(item, m["date"]), self.spec.date_format),
                        open=float(_rel(item, m["open"])),
                        high=float(_rel(item, m["high"])),
                        low=float(_rel(item, m["low"])),
                        close=float(_rel(item, m["close"])),
                        volume=float(_rel(item, m["volume"])) if "volume" in m else 0.0,
                    )
                )
            except (KeyError, TypeError, ValueError, FeedSpecError):
                # One malformed row must not lose the whole series.
                continue
        out.sort(key=lambda b: b.date)
        return out

    def _map_columns(self, payload: Any) -> list[Bar]:
        """Parallel-array shape: each field is a path to a list, zipped by index.

        This is the shape Yahoo's chart API, and many brokers, return:
        a shared timestamp array alongside per-field arrays (which may contain
        nulls). Rows with a null close are dropped rather than fabricated.
        """
        m = self.spec.field_map

        def col(key: str) -> list[Any]:
            return list(json_path(payload, m[key]))

        dates = col("date")
        opens = col("open")
        highs = col("high")
        lows = col("low")
        closes = col("close")
        volumes = col("volume") if "volume" in m else [0.0] * len(dates)

        n = min(len(dates), len(opens), len(highs), len(lows), len(closes), len(volumes))
        out: list[Bar] = []
        for i in range(n):
            if closes[i] is None or closes[i] != closes[i]:  # null / NaN close
                continue
            try:
                out.append(
                    Bar(
                        date=_to_iso(dates[i], self.spec.date_format),
                        open=float(opens[i]),
                        high=float(highs[i]),
                        low=float(lows[i]),
                        close=float(closes[i]),
                        volume=float(volumes[i] or 0),
                    )
                )
            except (TypeError, ValueError):
                continue
        out.sort(key=lambda b: b.date)
        return out


def _rel(item: Any, key: str) -> Any:
    """Resolve a key relative to one bar item, ignoring a leading `$.`."""
    if key.startswith("$"):
        return json_path(item, key)
    if isinstance(item, dict):
        if key not in item:
            raise KeyError(key)
        return item[key]
    raise FeedSpecError(f"cannot read {key!r} from {type(item).__name__}")


def _default_opener(req: urllib.request.Request, timeout: float) -> bytes:
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.read()


__all__ = [
    "FeedSpec", "FeedSpecError", "SpecFeed",
    "parse_spec", "load_spec_file", "json_path",
    "FEED_SPEC_DIRS",
]

FEED_SPEC_DIRS: tuple[str, ...] = (
    "feeds",
    "~/.trade-monitor/feeds",
    "~/.config/trade-monitor/feeds",
)
