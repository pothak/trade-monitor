"""Append-only event ledger: the audit trail.

Every meaningful thing the monitor does writes one row here, and nothing ever
updates or deletes a row. The point is that a user can ask "why did you
recommend this, and on what evidence, on that day" and get a complete answer
without re-running anything.

Design rules
------------
- Append-only. No UPDATE, no DELETE, ever. A correction is a new event.
- Self-describing. `subject` names what the event is about (usually a symbol);
  `thesis_id` links to the thesis when one exists.
- Replayable. `payload` carries the full detail, including rejections and the
  exact failing criterion, so history can be reconstructed.
- Ordered by a monotonic `seq`, not by timestamp. Two events in the same
  millisecond must still have a defined order.

Event kinds are namespaced strings. The vocabulary is deliberately small and
greppable:

    feed.pull           data fetched for a symbol
    feed.error          a pull failed
    detect.hit          a detector fired, with geometry
    detect.miss         no detector fired (carries the reason)
    detect.reject       a detector considered and rejected, with the failing gate
    score.computed      letters scored, grade and confidence assigned
    thesis.created      a thesis was built
    thesis.updated      confidence/grade changed versus a previous day
    watch.added         symbol entered the watchlist
    watch.held          symbol still qualifies on a subsequent day
    watch.dropped       symbol no longer qualifies, with the reason
    confirm.pending     candidate observed, not yet confirmed
    confirm.passed      candidate met the confirmation rule
    confirm.failed      candidate stopped meeting the rule
    recommend.issued    a recommendation was produced for the user
    recommend.withheld  a candidate that did not earn a recommendation, and why
    decision.recorded   a user (or process) decision: watch / enter / skip
    outcome.evaluated   forward result recorded for a thesis
    run.started         a scan run began
    run.finished        a scan run ended, with counters
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    run_id      INTEGER,
    kind        TEXT NOT NULL,
    subject     TEXT,
    thesis_id   TEXT,
    actor       TEXT NOT NULL DEFAULT 'monitor',
    summary     TEXT,
    payload     TEXT NOT NULL DEFAULT '{}',
    prev_hash   TEXT,
    row_hash    TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_subject ON events(subject, seq);
CREATE INDEX IF NOT EXISTS idx_events_kind    ON events(kind, seq);
CREATE INDEX IF NOT EXISTS idx_events_thesis  ON events(thesis_id, seq);
CREATE INDEX IF NOT EXISTS idx_events_run     ON events(run_id, seq);
"""

# The genesis link. The first row chains from this constant so even an empty
# ledger has a defined origin.
GENESIS = "0" * 64


def _canonical(
    *, seq: int, ts: float, run_id: int | None, kind: str,
    subject: str | None, thesis_id: str | None, actor: str,
    summary: str, payload: str,
) -> str:
    """Stable byte string for one event. Field order is fixed so the hash is
    reproducible across processes and Python versions."""
    return json.dumps(
        [seq, ts, run_id, kind, subject, thesis_id, actor, summary, payload],
        separators=(",", ":"), sort_keys=False,
    )


def _row_hash(prev_hash: str, canonical: str) -> str:
    h = hashlib.sha256()
    h.update(prev_hash.encode())
    h.update(canonical.encode())
    return h.hexdigest()


@dataclass(frozen=True)
class Event:
    seq: int
    ts: float
    kind: str
    subject: str | None
    thesis_id: str | None
    run_id: int | None
    actor: str
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def when(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.ts))

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq, "ts": self.ts, "when": self.when, "kind": self.kind,
            "subject": self.subject, "thesis_id": self.thesis_id, "run_id": self.run_id,
            "actor": self.actor, "summary": self.summary, "payload": self.payload,
        }


class Ledger:
    """Append-only event store. Shares a connection with the main Store when
    given one, so events and theses commit together."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.executescript(LEDGER_SCHEMA)
        self._ensure_hash_columns()
        from .migrations import migrate

        migrate(self._conn)
        self._conn.commit()

    def _ensure_hash_columns(self) -> None:
        """Adopt a pre-hash-chain DB by adding the columns (R2).

        Existing rows get NULL hashes; `verify` treats a NULL-hash prefix as
        'unchained history' rather than tampering, so an old ledger still opens.
        """
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(events)")}
        if "prev_hash" not in cols:
            self._conn.execute("ALTER TABLE events ADD COLUMN prev_hash TEXT")
        if "row_hash" not in cols:
            self._conn.execute("ALTER TABLE events ADD COLUMN row_hash TEXT")

    def _head(self) -> tuple[int, str]:
        """Last (seq, row_hash) in the chain."""
        row = self._conn.execute(
            "SELECT seq, row_hash FROM events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return 0, GENESIS
        return int(row[0]), (row[1] or GENESIS)

    @classmethod
    def at(cls, path: str | Path) -> Ledger:
        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(p), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return cls(conn)

    # -- write -----------------------------------------------------------

    def write(
        self,
        kind: str,
        *,
        subject: str | None = None,
        summary: str = "",
        payload: dict[str, Any] | None = None,
        thesis_id: str | None = None,
        run_id: int | None = None,
        actor: str = "monitor",
        commit: bool = True,
    ) -> int:
        ts = time.time()
        payload_json = json.dumps(payload or {}, default=str)
        # Chain: each row commits to the one before it, so altering or removing
        # any past row breaks every later hash.
        prev_seq, prev_hash = self._head()
        seq = prev_seq + 1
        rh = _row_hash(prev_hash, _canonical(
            seq=seq, ts=ts, run_id=run_id, kind=kind, subject=subject,
            thesis_id=thesis_id, actor=actor, summary=summary, payload=payload_json,
        ))
        cur = self._conn.execute(
            "INSERT INTO events "
            "(seq, ts, run_id, kind, subject, thesis_id, actor, summary, payload, "
            " prev_hash, row_hash) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                seq, ts, run_id, kind, subject, thesis_id, actor, summary,
                payload_json, prev_hash, rh,
            ),
        )
        if commit:
            self._conn.commit()
        return int(cur.lastrowid or seq)

    def write_many(self, events: Iterable[dict[str, Any]]) -> int:
        """Batch write inside one transaction. Used on the hot path so a scan
        does not pay a commit per event."""
        n = 0
        for ev in events:
            self.write(commit=False, **ev)
            n += 1
        self._conn.commit()
        return n

    # -- read ------------------------------------------------------------

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> Event:
        return Event(
            seq=row["seq"], ts=row["ts"], kind=row["kind"], subject=row["subject"],
            thesis_id=row["thesis_id"], run_id=row["run_id"], actor=row["actor"],
            summary=row["summary"] or "", payload=json.loads(row["payload"] or "{}"),
        )

    def events(
        self,
        *,
        subject: str | None = None,
        kind: str | None = None,
        thesis_id: str | None = None,
        run_id: int | None = None,
        since_seq: int | None = None,
        limit: int = 500,
    ) -> list[Event]:
        where: list[str] = []
        args: list[Any] = []
        if subject is not None:
            where.append("subject=?"); args.append(subject)
        if kind is not None:
            where.append("kind=?"); args.append(kind)
        if thesis_id is not None:
            where.append("thesis_id=?"); args.append(thesis_id)
        if run_id is not None:
            where.append("run_id=?"); args.append(run_id)
        if since_seq is not None:
            where.append("seq>?"); args.append(since_seq)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        cur = self._conn.execute(
            f"SELECT * FROM events {clause} ORDER BY seq ASC LIMIT ?", (*args, limit)
        )
        return [self._row_to_event(r) for r in cur.fetchall()]

    def timeline(self, subject: str, limit: int = 500) -> list[Event]:
        """One symbol's whole story, oldest first."""
        return self.events(subject=subject, limit=limit)

    def kinds(self, prefix: str | None = None) -> list[tuple[str, int]]:
        if prefix:
            cur = self._conn.execute(
                "SELECT kind, COUNT(*) c FROM events WHERE kind LIKE ? "
                "GROUP BY kind ORDER BY kind", (f"{prefix}%",),
            )
        else:
            cur = self._conn.execute(
                "SELECT kind, COUNT(*) c FROM events GROUP BY kind ORDER BY kind"
            )
        return [(r["kind"], int(r["c"])) for r in cur.fetchall()]

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])

    def verify(self) -> dict[str, Any]:
        """Walk the hash chain and report the first break, if any.

        Returns {ok, checked, chained, first_break_seq, reason}. A DB written
        before the chain existed has a NULL-hash prefix: that prefix is counted
        as `unchained` and skipped, not treated as tampering.
        """
        rows = self._conn.execute(
            "SELECT seq, ts, run_id, kind, subject, thesis_id, actor, summary, "
            "payload, prev_hash, row_hash FROM events ORDER BY seq ASC"
        ).fetchall()
        prev_hash = GENESIS
        checked = chained = 0
        for r in rows:
            checked += 1
            if r["row_hash"] is None:
                # Unchained history predates the feature; skip.
                continue
            chained += 1
            canonical = _canonical(
                seq=r["seq"], ts=r["ts"], run_id=r["run_id"], kind=r["kind"],
                subject=r["subject"], thesis_id=r["thesis_id"], actor=r["actor"],
                summary=r["summary"], payload=r["payload"],
            )
            expect_prev = prev_hash
            if r["prev_hash"] != expect_prev:
                return {"ok": False, "checked": checked, "chained": chained,
                        "first_break_seq": r["seq"],
                        "reason": "prev_hash does not link to the previous row"}
            if _row_hash(r["prev_hash"], canonical) != r["row_hash"]:
                return {"ok": False, "checked": checked, "chained": chained,
                        "first_break_seq": r["seq"],
                        "reason": "row_hash does not match the row contents"}
            prev_hash = r["row_hash"]
        return {"ok": True, "checked": checked, "chained": chained,
                "first_break_seq": None, "reason": "chain intact"}

    def tail(self, n: int = 20) -> list[Event]:
        cur = self._conn.execute(
            "SELECT * FROM events ORDER BY seq DESC LIMIT ?", (n,)
        )
        return [self._row_to_event(r) for r in reversed(cur.fetchall())]

    def narrative(self, subject: str) -> str:
        """Human-readable story of one symbol. This is what the UI shows."""
        lines: list[str] = []
        for ev in self.timeline(subject):
            marker = ev.kind.split(".")[-1].upper()
            lines.append(f"{ev.when}  {marker:<10} {ev.summary}")
        return "\n".join(lines)

    def close(self) -> None:
        self._conn.close()
