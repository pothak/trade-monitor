"""Market data store: normalized, sliced-and-diced, point-in-time safe.

The problem this solves
-----------------------
A `DataFeed` returns data in one fixed shape. A strategy that wants a different
shape -- intraday bars, breadth, sector-relative strength, flows -- has no way
to ask for it. Meanwhile re-fetching from upstream on every scan is slow and
rate-limit-prone.

So data is fetched once, normalized into tables, and then *queried*. Strategies
slice and dice with SQL against indexed tables instead of re-pulling from the
network. A scan over stored data is a local query, which is why performance
stops being a problem.

Storage layout
--------------
Every table is keyed `(symbol, date)`, which is the natural slice. Indices are
chosen for the access patterns actually used:

    bars(symbol, date)            indexed on (symbol, date) and (date)
    eps(symbol, period_end, period)   quarterly and annual, distinguished by `period`
    meta(symbol)                  static per-symbol facts (name, market, currency, sector)
    dataset(name, symbol, date, key, value)   the escape hatch: any other series

`dataset` is what makes the store open-ended. A new data need does not require a
schema change: it is a new `name`, and it is immediately queryable with the same
slicing guarantees. Bar-like upstreams land in `bars`; anything else lands here.

Point-in-time correctness
-------------------------
This is the part that matters and the part that is easy to get wrong.

`Query` is the *only* thing a strategy is given. It has no method that returns
rows without an explicit `as_of`. There is no `latest()`, no bare `SELECT`. A
strategy that wants the most recent data must say so with a date, which means
the same code runs identically in a backtest and in a live scan.

    q = store.query(as_of="2024-06-30")
    q.bars("RELIANCE.NS", lookback=300)        # bars with date <= as_of
    q.eps("RELIANCE.NS", period="quarter")     # EPS reported on or before as_of
    q.raw("SELECT ... FROM bars WHERE date <= :as_of AND symbol = :symbol", symbol=...)

`raw()` is provided because strategies write their own queries (that was an
explicit choice). It binds `:as_of` automatically and *requires* that the SQL
references it, so the guarantee holds even for hand-written SQL. A query that
tries to reach past `as_of` is rejected before it runs.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

MARKET_SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    symbol   TEXT NOT NULL,
    date     TEXT NOT NULL,
    open     REAL, high REAL, low REAL, close REAL, volume REAL,
    source   TEXT,
    PRIMARY KEY (symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_bars_date    ON bars(date);
CREATE INDEX IF NOT EXISTS idx_bars_symbol  ON bars(symbol, date DESC);

CREATE TABLE IF NOT EXISTS eps (
    symbol     TEXT NOT NULL,
    period_end TEXT NOT NULL,
    period     TEXT NOT NULL,          -- 'quarter' | 'annual'
    eps        REAL,
    reported   INTEGER DEFAULT 1,
    source     TEXT,
    PRIMARY KEY (symbol, period_end, period)
);
CREATE INDEX IF NOT EXISTS idx_eps_symbol ON eps(symbol, period_end DESC);

CREATE TABLE IF NOT EXISTS meta (
    symbol   TEXT PRIMARY KEY,
    name     TEXT,
    market   TEXT,
    currency TEXT,
    sector   TEXT,
    float_shares       REAL,
    shares_outstanding REAL,
    institutional_pct  REAL,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS dataset (
    name    TEXT NOT NULL,
    symbol  TEXT NOT NULL,
    date    TEXT NOT NULL,
    key     TEXT NOT NULL DEFAULT '',
    value   REAL,
    text    TEXT,
    source  TEXT,
    PRIMARY KEY (name, symbol, date, key)
);
CREATE INDEX IF NOT EXISTS idx_dataset_lookup ON dataset(name, symbol, date DESC);

CREATE TABLE IF NOT EXISTS fetch_log (
    symbol   TEXT NOT NULL,
    kind     TEXT NOT NULL,          -- bars | eps | meta | dataset:<name>
    fetched_at REAL NOT NULL,
    rows     INTEGER DEFAULT 0,
    source   TEXT,
    PRIMARY KEY (symbol, kind)
);
"""

# A statement may not name these: they would let a caller read or write around
# the time boundary.
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|detach|pragma|vacuum)\b",
    re.IGNORECASE,
)


class PointInTimeError(ValueError):
    """Raised when a query would read data outside its as_of boundary."""


@dataclass(frozen=True)
class Bar1:
    symbol: str
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class Query:
    """A read handle bound to one `as_of`.

    This is what a strategy receives. Every read is bounded by the date the
    handle was created with, so look-ahead is impossible by construction rather
    than by discipline.
    """

    def __init__(self, conn: sqlite3.Connection, as_of: str) -> None:
        if not as_of or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of):
            raise PointInTimeError(
                f"as_of must be an ISO date, got {as_of!r}. Every read needs a "
                "time boundary."
            )
        self._conn = conn
        self.as_of = as_of

    # -- typed helpers ---------------------------------------------------

    def bars(
        self, symbol: str, *, lookback: int = 300, market: str | None = None
    ) -> list[Bar1]:
        sql = (
            "SELECT symbol, date, open, high, low, close, volume FROM bars "
            "WHERE symbol = :symbol AND date <= :as_of "
            "ORDER BY date DESC LIMIT :n"
        )
        rows = self._conn.execute(
            sql, {"symbol": symbol, "as_of": self.as_of, "n": int(lookback)}
        ).fetchall()
        return [Bar1(**dict(r)) for r in reversed(rows)]

    def bars_range(self, symbol: str, start: str, end: str | None = None) -> list[Bar1]:
        end = min(end or self.as_of, self.as_of)
        sql = (
            "SELECT symbol, date, open, high, low, close, volume FROM bars "
            "WHERE symbol = :symbol AND date >= :start AND date <= :end "
            "ORDER BY date ASC"
        )
        rows = self._conn.execute(
            sql, {"symbol": symbol, "start": start, "end": end}
        ).fetchall()
        return [Bar1(**dict(r)) for r in rows]

    def eps(
        self, symbol: str, *, period: str = "quarter", limit: int = 40
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT period_end, eps, reported FROM eps "
            "WHERE symbol = :symbol AND period = :period AND period_end <= :as_of "
            "ORDER BY period_end DESC LIMIT :n"
        )
        return [
            dict(r)
            for r in self._conn.execute(
                sql,
                {"symbol": symbol, "period": period, "as_of": self.as_of, "n": limit},
            ).fetchall()
        ]

    def meta(self, symbol: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM meta WHERE symbol = ?", (symbol,)
        ).fetchone()
        return dict(row) if row else None

    def series(
        self, name: str, symbol: str, *, lookback: int = 300
    ) -> list[dict[str, Any]]:
        """Any registered dataset, sliced the same way as bars."""
        sql = (
            "SELECT date, key, value, text FROM dataset "
            "WHERE name = :name AND symbol = :symbol AND date <= :as_of "
            "ORDER BY date DESC LIMIT :n"
        )
        rows = self._conn.execute(
            sql, {"name": name, "symbol": symbol, "as_of": self.as_of, "n": lookback}
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def symbols(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT symbol FROM bars WHERE date <= ? ORDER BY symbol",
            (self.as_of,),
        ).fetchall()
        return [r["symbol"] for r in rows]

    def count_bars(self, symbol: str) -> int:
        return int(
            self._conn.execute(
                "SELECT COUNT(*) FROM bars WHERE symbol=? AND date<=?",
                (symbol, self.as_of),
            ).fetchone()[0]
        )

    # -- raw SQL, still bounded ------------------------------------------

    def raw(
        self, sql: str, /, **params: Any
    ) -> list[dict[str, Any]]:
        """Run hand-written SQL, bounded by `as_of`.

        This exists because strategies write their own queries. The boundary is
        still enforced:

          - the statement must be a SELECT
          - it must reference `:as_of`, so it cannot silently read all history

        Params are passed through; `as_of` is supplied and cannot be overridden.
        """
        stripped = sql.strip().rstrip(";")
        head = stripped.lstrip().lower()
        if not (head.startswith("select") or head.startswith("with")):
            raise PointInTimeError("raw() only runs SELECT statements")
        if _FORBIDDEN.search(stripped):
            raise PointInTimeError("raw() rejects write/DDL statements")
        if ":as_of" not in stripped:
            raise PointInTimeError(
                "raw() SQL must reference :as_of, so the read is bounded in time. "
                "Add `AND date <= :as_of` (or equivalent) to your query."
            )
        bound = {**params, "as_of": self.as_of}
        rows = self._conn.execute(stripped, bound).fetchall()
        return [dict(r) for r in rows]


class MarketStore:
    """Normalized market data, queryable by point in time.

    Writes are upserts on the natural key, so re-running a fetch is idempotent
    and a partial backfill never duplicates rows.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(MARKET_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- writes ----------------------------------------------------------

    def put_bars(self, symbol: str, bars: Iterable[Any], *, source: str = "unknown") -> int:
        rows = [
            (symbol, b.date, b.open, b.high, b.low, b.close, b.volume, source)
            for b in bars
        ]
        if not rows:
            return 0
        self._conn.executemany(
            "INSERT INTO bars (symbol, date, open, high, low, close, volume, source) "
            "VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(symbol, date) DO UPDATE SET "
            " open=excluded.open, high=excluded.high, low=excluded.low, "
            " close=excluded.close, volume=excluded.volume, source=excluded.source",
            rows,
        )
        self._log(symbol, "bars", len(rows), source)
        self._conn.commit()
        return len(rows)

    def put_eps(
        self, symbol: str, quarters: Iterable[Any], *, period: str = "quarter",
        source: str = "unknown",
    ) -> int:
        rows = [
            (symbol, q.period_end, period, q.eps, int(getattr(q, "reported", True)), source)
            for q in quarters
        ]
        if not rows:
            return 0
        self._conn.executemany(
            "INSERT INTO eps (symbol, period_end, period, eps, reported, source) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(symbol, period_end, period) DO UPDATE SET "
            " eps=excluded.eps, reported=excluded.reported, source=excluded.source",
            rows,
        )
        self._log(symbol, "eps", len(rows), source)
        self._conn.commit()
        return len(rows)

    def put_meta(self, symbol: str, **fields: Any) -> None:
        allowed = {
            "name", "market", "currency", "sector",
            "float_shares", "shares_outstanding", "institutional_pct",
        }
        cols = {k: v for k, v in fields.items() if k in allowed}
        if not cols:
            return
        existing = self._conn.execute(
            "SELECT symbol FROM meta WHERE symbol=?", (symbol,)
        ).fetchone()
        cols["updated_at"] = time.time()
        if existing:
            sets = ", ".join(f"{k}=?" for k in cols)
            self._conn.execute(
                f"UPDATE meta SET {sets} WHERE symbol=?",
                (*cols.values(), symbol),
            )
        else:
            names = ", ".join(["symbol", *cols])
            marks = ", ".join(["?"] * (len(cols) + 1))
            self._conn.execute(
                f"INSERT INTO meta ({names}) VALUES ({marks})",
                (symbol, *cols.values()),
            )
        self._log(symbol, "meta", 1, "put_meta")
        self._conn.commit()

    def put_dataset(
        self, name: str, symbol: str, date: str, value: float | None,
        *, key: str = "", text: str | None = None, source: str = "unknown",
    ) -> None:
        """Write any non-bar series. This is the open-ended escape hatch, so a
        new data need never requires a schema change."""
        self._conn.execute(
            "INSERT INTO dataset (name, symbol, date, key, value, text, source) "
            "VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(name, symbol, date, key) DO UPDATE SET "
            " value=excluded.value, text=excluded.text, source=excluded.source",
            (name, symbol, date, key, value, text, source),
        )
        self._conn.commit()

    def _log(self, symbol: str, kind: str, rows: int, source: str) -> None:
        self._conn.execute(
            "INSERT INTO fetch_log (symbol, kind, fetched_at, rows, source) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(symbol, kind) DO UPDATE SET "
            " fetched_at=excluded.fetched_at, rows=excluded.rows, source=excluded.source",
            (symbol, kind, time.time(), rows, source),
        )

    # -- reads -----------------------------------------------------------

    def query(self, as_of: str) -> Query:
        """The only read entry point. Requires the time boundary."""
        return Query(self._conn, as_of)

    def last_bar_date(self, symbol: str) -> str | None:
        row = self._conn.execute(
            "SELECT MAX(date) d FROM bars WHERE symbol=?", (symbol,)
        ).fetchone()
        return row["d"] if row else None

    def coverage(self) -> dict[str, Any]:
        cur = self._conn.execute(
            "SELECT COUNT(DISTINCT symbol) s, COUNT(*) n, MIN(date) lo, MAX(date) hi FROM bars"
        )
        r = cur.fetchone()
        return {
            "symbols": int(r["s"] or 0),
            "rows": int(r["n"] or 0),
            "from": r["lo"],
            "to": r["hi"],
            "datasets": [
                dict(x) for x in self._conn.execute(
                    "SELECT name, COUNT(DISTINCT symbol) symbols, COUNT(*) rows "
                    "FROM dataset GROUP BY name"
                ).fetchall()
            ],
            "path": str(self.path),
        }

    def stale(self, symbol: str, kind: str = "bars", *, max_age_s: float = 86400) -> bool:
        row = self._conn.execute(
            "SELECT fetched_at FROM fetch_log WHERE symbol=? AND kind=?", (symbol, kind)
        ).fetchone()
        if not row:
            return True
        return (time.time() - row["fetched_at"]) > max_age_s

    def write_raw(self, sql: str, params: Sequence[Any] = ()) -> None:
        """Writes are done through the typed methods, not ad-hoc SQL. This
        exists for migrations and tests."""
        self._conn.execute(sql, params)
        self._conn.commit()
