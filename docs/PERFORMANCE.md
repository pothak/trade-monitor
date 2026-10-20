# Performance

Measured, not assumed. Every number here was produced by a script in this repo
or is directly reproducible; where a claim is structural rather than measured,
it says so.

Newest entries last. This file exists so the recurring "should we rewrite the
engine in a faster language?" question is answered by data rather than feel.

---

## P-1 — Where the time actually goes

**Date** 2026-10-20
**Method** timed a cold and warm price fetch, then 200 iterations of pattern
detection and strategy scoring, over 8 real NSE symbols (`RELIANCE.NS`,
`TCS.NS`, `INFY.NS`, `HDFCBANK.NS`, `ITC.NS`, `SBIN.NS`, `LT.NS`,
`ICICIBANK.NS`), 300 bars each.

| Stage | Per symbol | Notes |
|---|---|---|
| **Network price fetch (cold)** | **297.9 ms** | the dominant cost |
| Network price fetch (warm/cached) | **0.00 ms** | the cache is doing its job |
| Pattern detect (cup-with-handle) | 0.194 ms | 194 µs |
| Strategy score (momentum_rank) | 0.010 ms | 10 µs |
| **Compute total** | **0.204 ms** | detect + score |

**Compute is ~0.07% of a cold scan.**

```
network   297.9   ms  ████████████████████████████████████████  99.93%
compute     0.204 ms  ▏                                            0.07%
```

### Why this matters

Every real bottleneck is **network I/O**. Compute is microseconds against a
network call measured in hundreds of milliseconds.

This answers the "rewrite in Rust" question directly: rewriting the *entire*
compute path in a faster language has a theoretical ceiling of shaving 0.2 ms
off a 298 ms operation — **a 0.07% speedup.** Rust does not make a remote server
answer faster.

### Where the speed levers actually are

| Lever | Status | Potential |
|---|---|---|
| Gate expensive work (cheap-filter-first) | **built** | ~10× on gated names (fundamentals never fetched) |
| Parallel fetch (8 workers) | **built** | ~8× over serial |
| Cache / local store (avoid re-fetch) | **built** | effectively ∞ on warm data |
| Batch multi-ticker download | **not built** | fewer round trips per symbol (ENG: fetch batching) |
| Rewrite compute in Rust | — | **0.07%** |

The wins are all in **data movement**, not arithmetic.

---

## P-2 — Compute scales, but network scales worse

**Date** 2026-10-20
**Method** extrapolation from P-1 (structural, not re-measured).

| Universe | Compute (est.) | Network (est., cold, serial) |
|---|---|---|
| 8 symbols | 1.6 ms | 2.4 s |
| 500 symbols | 0.10 s | ~2.5 min |
| 2,500 symbols | 0.51 s | ~12 min |

At full-universe scale, compute is ~0.07% of wall time. Even at 2,500 symbols,
a Rust rewrite saves **half a second** against **twelve minutes** of network
wait. The correct optimization is batching and caching the fetch, not the
language.

### The one place compute could ever matter

**Streaming.** A batch scan waits on the network once per symbol; a stream has
no per-tick network wait. So per-tick indicator math over thousands of symbols
at high cadence is the *only* workload where compute could dominate.

Even there, the first fixes are a queue and a window cache — not a language.

---

## Reproducing

```bash
python - <<'PY'
import time
from trade_monitor.datafeed import REGISTRY as FEEDS
from trade_monitor.patterns import REGISTRY as PATTERNS
feed = FEEDS.create("yfinance"); det = PATTERNS.create("cup_with_handle")
syms = ["RELIANCE.NS","TCS.NS","INFY.NS","HDFCBANK.NS","ITC.NS","SBIN.NS"]
t=time.perf_counter(); series={s:feed.price_series(s,300) for s in syms}
print("cold fetch/sym ms:", (time.perf_counter()-t)/len(syms)*1000)
N=200; t=time.perf_counter()
for _ in range(N):
    for s in syms: det.detect(series[s])
print("detect us/sym:", (time.perf_counter()-t)/N/len(syms)*1e6)
PY
```

## Decision

**The engine stays in Python.** The plugin API — a DIY investor writing one
readable file — is the product, and it is worth more than a 0.07% compute
saving. If a hot path is ever *measured* to dominate, the escape hatches, in
order, are: numpy vectorization → Cython/mypyc → a Rust extension (PyO3) for
that one function. Never a whole-app rewrite.

This decision is revisited **only** with a new measurement in this file that
contradicts P-1.
