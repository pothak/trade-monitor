"""Local SQLite store.

Everything the system observes lands here, permanently:

  observations   raw scores per symbol per run (the full population, not just
                 hits) so we can ask "what did we see in this name last month"
                 rather than only remembering winners
  theses         canonical records that survived scoring
  decisions      your approve/reject, with timestamps
  outcomes       forward returns measured after the fact, filled in lazily
  artifacts      chart paths keyed by thesis

Design notes:
  - Append-only for observations. Nothing is overwritten, so a re-scan
    accumulates rather than replaces.
  - `outcomes` are computed on demand from stored `as_of` + price data, never
    assumed at signal time.
  - WAL mode so a long scan can write while the UI reads.
  - No ORM; plain sqlite3 keeps the package dependency-light.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

from ..contracts import Thesis

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    REAL NOT NULL,
    finished_at   REAL,
    market        TEXT,
    strategy      TEXT,
    plan_total    INTEGER DEFAULT 0,
    scanned       INTEGER DEFAULT 0,
    errors        INTEGER DEFAULT 0,
    hits          INTEGER DEFAULT 0,
    config        TEXT
);

CREATE TABLE IF NOT EXISTS observations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER REFERENCES runs(id),
    symbol        TEXT NOT NULL,
    market        TEXT,
    as_of         TEXT NOT NULL,
    score         REAL,
    grade         TEXT,
    pattern       TEXT,
    pattern_strength REAL,
    confidence    INTEGER,
    evidence      TEXT,
    detail        TEXT,
    UNIQUE(run_id, symbol)
);
CREATE INDEX IF NOT EXISTS idx_obs_symbol ON observations(symbol, as_of DESC);
CREATE INDEX IF NOT EXISTS idx_obs_asof   ON observations(as_of DESC);

CREATE TABLE IF NOT EXISTS theses (
    id            TEXT PRIMARY KEY,
    created_at    REAL NOT NULL,
    run_id        INTEGER REFERENCES runs(id),
    symbol        TEXT NOT NULL,
    market        TEXT,
    strategy      TEXT,
    as_of         TEXT NOT NULL,
    grade         TEXT,
    confidence    INTEGER,
    payload       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_theses_symbol ON theses(symbol, created_at DESC);

CREATE TABLE IF NOT EXISTS decisions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    thesis_id     TEXT NOT NULL REFERENCES theses(id),
    created_at    REAL NOT NULL,
    decision      TEXT NOT NULL,          -- approve | reject
    note          TEXT,
    snapshot      TEXT
);
CREATE INDEX IF NOT EXISTS idx_dec_thesis ON decisions(thesis_id);

CREATE TABLE IF NOT EXISTS outcomes (
    thesis_id     TEXT PRIMARY KEY REFERENCES theses(id),
    evaluated_at  REAL,
    horizon_days  INTEGER,
    entry_price   REAL,
    return_pct    REAL,
    max_favorable REAL,
    max_adverse   REAL,
    pivot_hit     INTEGER,
    stop_hit      INTEGER,
    outcome       TEXT,                   -- target | stop | timeout | pending
    method        TEXT
);

CREATE TABLE IF NOT EXISTS artifacts (
    thesis_id     TEXT NOT NULL REFERENCES theses(id),
    kind          TEXT NOT NULL,          -- chart | report
    path          TEXT NOT NULL,
    created_at    REAL NOT NULL,
    PRIMARY KEY (thesis_id, kind)
);
"""


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # -- runs ------------------------------------------------------------

    def start_run(
        self,
        *,
        market: str,
        strategy: str,
        plan_total: int,
        config: dict[str, Any] | None = None,
    ) -> int:
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO runs (started_at, market, strategy, plan_total, config) "
                "VALUES (?,?,?,?,?)",
                (time.time(), market, strategy, plan_total, json.dumps(config or {})),
            )
        return int(cur.lastrowid or 0)

    def finish_run(self, run_id: int, *, scanned: int, errors: int, hits: int) -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE runs SET finished_at=?, scanned=?, errors=?, hits=? WHERE id=?",
                (time.time(), scanned, errors, hits, run_id),
            )

    def recent_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in cur.fetchall()]

    # -- observations ----------------------------------------------------

    def record_observation(
        self,
        *,
        run_id: int | None,
        symbol: str,
        market: str,
        as_of: str,
        score: float | None,
        grade: str | None,
        confidence: int | None = None,
        pattern: str | None = None,
        pattern_strength: float | None = None,
        evidence: Sequence[dict[str, Any]] | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """One row per symbol per run. Called for every symbol scanned,
        including misses, so we keep a population rather than a highlight reel."""
        with self.tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO observations "
                "(run_id, symbol, market, as_of, score, grade, pattern, "
                " pattern_strength, confidence, evidence, detail) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    symbol,
                    market,
                    as_of,
                    score,
                    grade,
                    pattern,
                    pattern_strength,
                    confidence,
                    json.dumps(list(evidence or [])),
                    json.dumps(detail or {}),
                ),
            )

    def observations(self, symbol: str, limit: int = 200) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT * FROM observations WHERE symbol=? ORDER BY as_of DESC, id DESC LIMIT ?",
            (symbol, limit),
        )
        return [dict(r) for r in cur.fetchall()]

    def observation_history(
        self, symbol: str, since: str | None = None
    ) -> list[dict[str, Any]]:
        if since:
            cur = self._conn.execute(
                "SELECT as_of, score, grade, pattern, pattern_strength, confidence "
                "FROM observations WHERE symbol=? AND as_of>=? ORDER BY as_of ASC",
                (symbol, since),
            )
        else:
            cur = self._conn.execute(
                "SELECT as_of, score, grade, pattern, pattern_strength, confidence "
                "FROM observations WHERE symbol=? ORDER BY as_of ASC",
                (symbol,),
            )
        return [dict(r) for r in cur.fetchall()]

    def first_seen(self, symbol: str) -> dict[str, Any] | None:
        """Earliest observation for a symbol. Lets us distinguish a fresh
        setup from one we have been watching for weeks."""
        cur = self._conn.execute(
            "SELECT as_of, grade, pattern, pattern_strength FROM observations "
            "WHERE symbol=? ORDER BY as_of ASC LIMIT 1",
            (symbol,),
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def seen_count(self, symbol: str) -> int:
        cur = self._conn.execute(
            "SELECT COUNT(DISTINCT as_of) FROM observations WHERE symbol=?", (symbol,)
        )
        return int(cur.fetchone()[0])

    # -- theses ----------------------------------------------------------

    def save_thesis(self, thesis: Thesis, run_id: int | None = None) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO theses "
                "(id, created_at, run_id, symbol, market, strategy, as_of, grade, "
                " confidence, payload) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    thesis.id,
                    time.time(),
                    run_id,
                    thesis.symbol,
                    thesis.market,
                    thesis.strategy,
                    thesis.as_of,
                    thesis.grade,
                    thesis.confidence,
                    json.dumps(thesis.to_dict()),
                ),
            )

    def save_theses(self, theses: Sequence[Thesis], run_id: int | None = None) -> None:
        for t in theses:
            self.save_thesis(t, run_id)

    def get_thesis(self, thesis_id: str) -> dict[str, Any] | None:
        cur = self._conn.execute("SELECT payload FROM theses WHERE id=?", (thesis_id,))
        row = cur.fetchone()
        return json.loads(row["payload"]) if row else None

    def list_theses(
        self,
        *,
        market: str | None = None,
        min_grade: str | None = None,
        min_confidence: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        order = {"A": 0, "B": 1, "C": 2, "D": 3, "F": 4}
        rows = self._conn.execute(
            "SELECT payload FROM theses ORDER BY created_at DESC"
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            t = json.loads(row["payload"])
            if market and t.get("market") != market:
                continue
            if min_grade and order.get(t.get("grade", "F"), 4) > order.get(min_grade, 4):
                continue
            if min_confidence is not None and int(t.get("confidence", 0)) < min_confidence:
                continue
            out.append(t)
            if len(out) >= limit:
                break
        return out

    # -- decisions -------------------------------------------------------

    def decide(
        self, thesis_id: str, decision: str, note: str | None = None,
        snapshot: dict[str, Any] | None = None,
    ) -> int:
        if decision not in {"approve", "reject"}:
            raise ValueError("decision must be approve|reject")
        if self.get_thesis(thesis_id) is None:
            raise KeyError(f"unknown thesis {thesis_id}")
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO decisions (thesis_id, created_at, decision, note, snapshot) "
                "VALUES (?,?,?,?,?)",
                (thesis_id, time.time(), decision, note, json.dumps(snapshot or {})),
            )
        return int(cur.lastrowid or 0)

    def decisions(self, thesis_id: str) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT * FROM decisions WHERE thesis_id=? ORDER BY created_at ASC", (thesis_id,)
        )
        return [dict(r) for r in cur.fetchall()]

    def latest_decision(self, thesis_id: str) -> dict[str, Any] | None:
        rows = self.decisions(thesis_id)
        return rows[-1] if rows else None

    # -- outcomes --------------------------------------------------------

    def save_outcome(
        self,
        thesis_id: str,
        *,
        horizon_days: int,
        entry_price: float,
        return_pct: float,
        max_favorable: float | None = None,
        max_adverse: float | None = None,
        pivot_hit: bool | None = None,
        stop_hit: bool | None = None,
        outcome: str = "pending",
        method: str = "close",
    ) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO outcomes (thesis_id, evaluated_at, horizon_days, "
                "entry_price, return_pct, max_favorable, max_adverse, pivot_hit, "
                "stop_hit, outcome, method) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    thesis_id,
                    time.time(),
                    horizon_days,
                    entry_price,
                    return_pct,
                    max_favorable,
                    max_adverse,
                    None if pivot_hit is None else int(pivot_hit),
                    None if stop_hit is None else int(stop_hit),
                    outcome,
                    method,
                ),
            )

    def outcome(self, thesis_id: str) -> dict[str, Any] | None:
        cur = self._conn.execute(
            "SELECT * FROM outcomes WHERE thesis_id=?", (thesis_id,)
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def pending_outcomes(self, min_age_days: int = 5) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT t.id, t.symbol, t.market, t.as_of, t.payload, t.created_at "
            "FROM theses t LEFT JOIN outcomes o ON o.thesis_id = t.id "
            "WHERE o.thesis_id IS NULL "
            "AND (julianday('now') - julianday(t.created_at)) >= ?",
            (min_age_days,),
        )
        return [dict(r) for r in cur.fetchall()]

    def calibration(self, min_confidence: int | None = None) -> dict[str, Any]:
        """Hit rate and expectancy by grade, so thresholds can be tuned on
        evidence rather than intuition."""
        cur = self._conn.execute(
            "SELECT t.grade, o.outcome, o.return_pct, t.confidence "
            "FROM theses t JOIN outcomes o ON o.thesis_id = t.id"
        )
        buckets: dict[str, dict[str, Any]] = {}
        for row in cur.fetchall():
            if min_confidence is not None and (row["confidence"] or 0) < min_confidence:
                continue
            b = buckets.setdefault(
                row["grade"] or "?", {"n": 0, "wins": 0, "rets": [], "outcomes": {}}
            )
            b["n"] += 1
            b["outcomes"][row["outcome"] or "pending"] = (
                b["outcomes"].get(row["outcome"] or "pending", 0) + 1
            )
            if row["return_pct"] is not None:
                b["rets"].append(float(row["return_pct"]))
            if (row["outcome"] == "target") or (
                row["return_pct"] is not None and float(row["return_pct"]) > 0
            ):
                b["wins"] += 1
        summary: dict[str, Any] = {}
        for grade, b in sorted(buckets.items()):
            rets = b["rets"]
            summary[grade] = {
                "n": b["n"],
                "hit_rate": round(b["wins"] / b["n"], 4) if b["n"] else 0.0,
                "avg_return": round(sum(rets) / len(rets), 3) if rets else None,
                "outcomes": b["outcomes"],
            }
        return summary

    # -- artifacts -------------------------------------------------------

    def save_artifact(self, thesis_id: str, kind: str, path: str) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO artifacts (thesis_id, kind, path, created_at) "
                "VALUES (?,?,?,?)",
                (thesis_id, kind, str(path), time.time()),
            )

    def artifact(self, thesis_id: str, kind: str) -> str | None:
        cur = self._conn.execute(
            "SELECT path FROM artifacts WHERE thesis_id=? AND kind=?", (thesis_id, kind)
        )
        row = cur.fetchone()
        return row["path"] if row else None

    def stats(self) -> dict[str, Any]:
        def count(table: str) -> int:
            return int(self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

        cur = self._conn.execute("SELECT COUNT(DISTINCT symbol) FROM observations")
        symbols = int(cur.fetchone()[0])
        return {
            "runs": count("runs"),
            "observations": count("observations"),
            "symbols_seen": symbols,
            "theses": count("theses"),
            "decisions": count("decisions"),
            "outcomes": count("outcomes"),
            "path": str(self.path),
        }