# Data model

**Status** PARTIAL — all three stores and the point-in-time guarantee are built;
the ledger **hash chain** (R2) is not.

## Three stores, one job each

| Store | Holds | Table |
|---|---|---|
| `Store` | engine state: runs, observations, theses, decisions, outcomes, artifacts | `engine.db` |
| `MarketStore` | normalized, point-in-time market data | `market.db` |
| `Ledger` | append-only audit trail | `ledger.db` |

They can share a file or live side by side; nothing depends on them being
separate. All live under `~/.trade-monitor/` by default.

---

## `MarketStore` — normalized and point-in-time safe

```
bars(symbol, date)              open high low close volume source
eps(symbol, period_end, period) period = 'quarter' | 'annual'
meta(symbol)                    name market currency sector float shares instit_pct
dataset(name, symbol, date, key)  the escape hatch: any other series
fetch_log(symbol, kind)         when each symbol was last fetched
```

**Indices** are chosen for the actual access patterns: `(symbol, date)` for a
slice, `(date)` for a cross-section. A query is a B-tree lookup, not a scan.

**Upserts on the natural key**, so re-fetching is idempotent and a partial
backfill never duplicates.

**`dataset` is open-ended.** A new data need (breadth, flows, sector strength)
is a new `name` — no schema change, and it inherits the same slicing guarantees.

### The point-in-time rule (Principle P1)

A strategy never touches a bare connection. It gets a **`Query` handle bound to
one `as_of`**, and **no method on it returns rows without a time boundary**:

```python
q = store.query(as_of="2024-06-30")
q.bars("RELIANCE.NS", lookback=300)       # bars with date <= as_of
q.eps("RELIANCE.NS", period="quarter")     # EPS reported on or before as_of
q.series("breadth", "NIFTY", lookback=60)  # any dataset, same boundary
q.raw("SELECT ... WHERE date <= :as_of AND symbol = :symbol", symbol=...)
```

`raw()` exists because strategies write their own SQL (an explicit choice). It
still enforces the boundary: the statement must be a `SELECT`, must be free of
write/DDL keywords, and **must reference `:as_of`**. Look-ahead is impossible by
construction rather than by discipline.

**Verified.** A query without `as_of` is rejected; `raw()` without `:as_of`,
`raw()` with a `DELETE`, and `raw()` with an `UPDATE` are all rejected.

---

## `Store` — engine state

```
runs          one row per scan (market, strategy, plan_total, scanned, hits)
observations  one row per (run, symbol) -- misses recorded too
theses        the full Thesis, as JSON
decisions     what the user did
outcomes      how it turned out
artifacts     rendered charts
```

`observations` records **misses as well as hits**, so the population is complete
and `calibration()` can measure a strategy against everything it saw, not just
its winners. `UNIQUE(run_id, symbol)`.

---

## `Ledger` — the audit trail

Append-only. Never updated, only appended. Ordered by a monotonic `seq`, **not
by timestamp** — two events in the same second must still have a deterministic
order.

```
events(seq, ts, run_id, kind, subject, thesis_id, actor, summary, payload)
```

Every decision is replayable event by event. The event vocabulary:

```
feed.pull        run.started        strategy.loaded
detect.hit       run.finished       strategy.skipped
detect.miss      score.computed     strategy.shadowed
detect.reject    thesis.created     market.refreshed
watch.added      thesis.updated     market.unavailable
watch.held       confirm.pending
watch.dropped    confirm.passed     recommend.issued
                 confirm.failed     recommend.withheld
                 decision.recorded  outcome.evaluated
```

### What makes it *verifiable*, not merely stored (Principle R2)

Append-only stops accidents, not a determined rewrite. To let a user **prove**
the record, the ledger needs:

- **hash chaining** — each event embeds the hash of its predecessor, so any
  gap, reorder or edit is detectable
- **content hashing** — each event hashes the data it cites, so "the close was
  1202" is checkable against the stored bar
- **verifiable replay** — re-running from the ledger reproduces the decisions

**This is not built.** The events exist and are ordered; the chain does not.
It is the highest-value item under Principle R2.

---

## Free data is imperfect, and the model says so

No subscription means free sources, and free sources are incomplete (measured:
2023 entirely missing from NSE MCP; `institutional_holders` empty for every
`.NS` symbol). The model **labels** the gap rather than papering over it:

- `Fundamentals.degraded` lists what the provider did not return
- confidence is scaled by data coverage
- the UI shows data quality next to every recommendation

See [PHILOSOPHY.md](PHILOSOPHY.md).
