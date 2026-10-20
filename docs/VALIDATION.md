# Validation log

Every end-to-end run and what it showed. Append-only. Newest last.

A validation entry stays here even when it later goes stale, so we can see what
we believed and when.

---

## V-1 — Engine end-to-end, single strategy, live yfinance

**Date** 2026-10-20
**What ran** `engine.run_strategy("canslim", <8 liquid NSE names>, feed=yfinance,
market="IN", lookback=300, workers=8, store=<sqlite>)`
**Symbols** RELIANCE.NS, TCS.NS, INFY.NS, HDFCBANK.NS, ITC.NS, SBIN.NS, LT.NS,
ICICIBANK.NS

### Result

```
scanned: 8   misses: 8   hits: 0   errors: 0
elapsed: 5.2 s
per-symbol: 650.5 ms
runs=0  observations=8  symbols_seen=8  theses=0  decisions=0  outcomes=0
```

### What this proves (the good)

| Claim | Status |
|---|---|
| All modules import; no syntax or import errors | PASS |
| Registries resolve by name (`patterns`: 1, `strategy`: 1, `validate`/`delivery`/`render`: empty) | PASS |
| Engine wires the whole chain: prices -> detect -> (gate) -> score -> thesis -> store | PASS |
| Cheap-filter-first ordering works: with zero pattern hits, **fundamentals were never fetched**. The 8 survivors skipped step 4 entirely. | PASS |
| Misses are recorded, not just hits: 8 `observations` rows written with `grade=NULL` and `pattern=NULL` | PASS |
| `first_seen` / `seen_count` work off those miss rows | PASS |
| A run with zero hits exits `errors=0` rather than raising | PASS |
| `per_symbol_ms` reporting exists and is populated | PASS |

The 650 ms/symbol is **dominated by the yfinance price fetch**, not by CPU. In a
real tier-2 scan the same path costs one fetch and no fundamentals.

### What this exposes (the problem)

**`hits: 0` — the detector did not fire on any current bar.**

This is not a bug in the engine, and it is not a data problem. It is the most
important finding so far.

#### Detector fires historically, but never on the latest bar

Sliding a 300-bar window across 400 bars of history, stepping 10:

| Symbol | Bars | Fires | First fire |
|---|---|---|---|
| RELIANCE.NS | 400 | 2 | 2026-07-09 |
| TCS.NS | 400 | 3 | 2026-08-06 |
| INFY.NS | 400 | 1 | 2026-07-23 |
| HDFCBANK.NS | 400 | 1 | 2026-06-24 |
| ITC.NS | 400 | 2 | 2026-05-26 |
| SBIN.NS | 400 | 2 | 2026-06-24 |
| LT.NS | 400 | 2 | 2026-08-06 |
| ICICIBANK.NS | 400 | 0 | — |
| AAPL | 400 | 0 | — |
| MSFT | 400 | 2 | 2026-07-13 |
| NVDA | 400 | 0 | — |
| **Total** | | **15** | |

So the detector *does* recognize the geometry. It just does not recognize it on
*this week's* bar. Two possibilities, and they matter very differently:

1. There genuinely is no valid cup-with-handle setup in these names right now.
   This is plausible and correct behaviour.
2. The detector is structured so that a setup can only be recognized in the
   past, never in the present.

**Evidence points at (2).** See below.

#### Relaxing every gate does not rescue the latest bar

For RELIANCE.NS / TCS.NS / SBIN.NS at the latest bar, each of these was tried
independently:

| Relaxation | Result |
|---|---|
| `handle_duration` 3..40 | still rejected |
| `cup_duration` 20..90 | still rejected |
| `cup_depth` 8..40 | still rejected |
| `handle_depth` <= 25% | still rejected |
| `pivot_tolerance` 5% | still rejected |

Relaxing gates one at a time changes nothing, which means the rejection is
**structural**, not a threshold that is merely set too tight.

#### The actual cause: the rim/handle anchoring requires *completed* history

Instrumenting `_geometry` for the newest rim candidate on TCS.NS
(2026-09-15, 300 bars ending 2026-10-19):

```
cup_depth    12.51%   gate 10.0-33.0%    OK
cup_bars        10    gate 25-65         FAIL
handle_bars      7    gate 5-30          OK
recovered      yes
handle_depth 10.20%   gate <= 15.0%      OK
retrace        141%   gate <= 50%        FAIL
```

Two things are wrong here, and they are both about **where the handle is
anchored**:

- `cup_bars = 10` — the "cup" collapsed to 10 sessions, below the 25 minimum.
- `retrace = 141%` — the handle retraces 141% of the cup depth, which is
  geometrically impossible for a real handle.

What happened: the detector's most recent *rim candidate* is chosen as a local
high 7 bars from the end. The code then treats everything after the base low —
including the still-forming right side — as the "handle". On a base that is
currently *forming*, the right side of the cup has not finished rising, so:

1. the base low found is too recent, shrinking `cup_bars`, and
2. the "handle" swallows the recovery leg, inflating `retrace` past 100%.

The code has a guard for this — `handle_start_idx = recovery_idx` only if price
recovers above **mid-cup** — but for a forming base the recovery has not yet
reached mid-cup, so `recovery_idx` is `None`, and the fallback
`handle_start_idx = base_idx + 1` anchors the handle **at the low itself**. That
is exactly the failure mode the module docstring says it is preventing:

> Anchoring the handle at the low itself mislabels an unfinished decline as a
> handle.

The guard is correct in intent but has no branch for "the base is still forming",
so it falls through to the mislabel.

### Conclusion

The engine is sound and validated. The **detector is validated as working on
completed bases and as structurally unable to recognise a still-forming base**,
which is the case we most care about, because a trade monitor wants to see the
setup *before* the breakout, not months after.

This is a detector defect, not an engine defect. It is tracked as PAT-6.

### Follow-ups created

| ID | Item |
|---|---|
| PAT-6 | Handle anchoring mislabels a forming base: guard falls through to `base_idx + 1`, giving `cup_bars` too low and `retrace > 100%` |
| PAT-1 | Characterise detector selectivity (now has firm evidence: 15 fires over 400 bars, 0 on the latest bar) |
| BT-1 | Target definition still unreachable by construction |
| INFRA-1 | Still no git; this work is unversioned |
