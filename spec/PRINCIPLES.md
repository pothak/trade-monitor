# Principles

**Status** PARTIAL — P1, P2, P5 are enforced by construction; P3, P4, P6, P7 are partly built.

These are invariants, not preferences. When a change would break one, the
change is wrong. Each says how it is enforced, because a principle without
enforcement is a slogan.

---

## P1 — No look-ahead, ever

**A decision may only use data that existed at decision time.**

This is the difference between a backtest that predicts and one that lies.

*Enforcement.* Reads go through `MarketStore.query(as_of=...)`. The `Query`
handle has no method that returns rows without a time boundary, and its `raw()`
method rejects SQL that does not reference `:as_of`. Look-ahead is impossible by
construction, not by discipline.

*Verify.* Attempt a query without `as_of` → rejected. See
[DATA-MODEL.md](DATA-MODEL.md).

---

## P2 — One code path for live and backtest

**The stream and the backtest must run the same strategy code on the same input
shape.**

If live and backtest differ, every backtest number stops predicting anything.

*Enforcement.* The stream never makes decisions. It fills a rolling window;
decisions run at bar close by handing that window to the same engine the
backtest uses. See [STREAMING.md](STREAMING.md).

*Verify.* Same symbol, same day → same grade from `live` and `batch`.

---

## P3 — Expensive work is gated

**Never spend fundamentals cost on a name that cannot produce a thesis.**

Fundamentals cost ~10× a price fetch. Most names fail a cheap gate first.

*Enforcement.* The engine is ordered cheapest-first: fetch prices → detect
patterns → **if no pattern, stop** → fetch fundamentals only for survivors.
A price-only strategy skips fundamentals entirely via `needs_fundamentals`.

*Verify.* Scan 8 names where none match → 0 fundamentals fetches.

---

## P4 — Every decision is auditable

**Any recommendation can be replayed event by event.**

*Enforcement.* An append-only ledger records every step (`feed.pull`,
`detect.hit`, `score.computed`, `thesis.created`, `recommend.issued`, …). It is
never updated, only appended, so history cannot be rewritten.

*Verify.* Given a thesis, list its ledger events in order.

---

## P5 — Data is point-in-time;

**a re-fetched source must not silently change a past decision.**

Providers revise history (splits, corrections). A decision made on old data must
stay reproducible.

*Enforcement.* Data lands in a normalized store keyed on `(symbol, date)`, and
each fetch is logged. A revised bar upserts; the ledger still records what was
seen when.

---

## P6 — Degrade, never crash

**A missing source, a broken strategy, a bad tick must not stop the monitor.**

A monitor that dies is worse than one that reports "data unavailable".

*Enforcement.* Strategies are loaded fault-isolated: a bad file is skipped and
logged, the rest load. A failing indicator records its error; the others keep
computing. A failing symbol does not abort a scan.

*Verify.* Load a broken strategy file → it is skipped, engine continues.

---

## P7 — Content is not glued to mechanism

**Strategies are plugins. The app must not hard-code any one of them.**

CAN SLIM is the first plugin, not the architecture.

*Enforcement.* Strategies are discovered from a directory at runtime and
selected by name from a registry. The contracts live in a package a strategy can
import *without* importing the engine. See [PLUGINS.md](PLUGINS.md).

---

## P8 — Fail loudly on ambiguity, never on a guess

**If a required input is missing, say so; do not invent it.**

A strategy that needs fundamentals and cannot get them must report reduced
confidence, not fabricate numbers.

*Enforcement.* `Fundamentals.degraded` records what the provider did not return.
Confidence is scaled by data coverage.

*Verify.* Score a symbol with no EPS → confidence drops, thesis says why.
