# Streaming

**Status** PARTIAL — rolling windows, indicators, bar-close wiring and live/batch
parity are built and tested. A **polling feeder that emits StreamEvents is not
built**, so nothing drives the stream yet.

## The two cadences

The entire design turns on this: **ticks are cheap and frequent; decisions are
expensive and rare.**

```
  provider ──poll──► StreamEvent ──► RollingWindow ──► indicators
   (yfinance)         {symbol,         (per symbol)      (update per tick)
                       ts, close,        bounded,
                       volume}          ordered,
                                        deduped}
                                            │
                              [ a new day starts ]
                                            │
                                            ▼
                       on_bar_close(symbol, day, window)
                                            │
                                            ▼
                       batch engine:  detect -> score -> thesis -> ledger
```

| Cadence | Runs | Cost | Decides? |
|---|---|---|---|
| per tick (~seconds) | window update + indicators | pure CPU | **no** |
| per bar close (daily) | detect → score → thesis | the strategy | **yes** |

## Why decisions never happen per tick

Three reasons, in order of importance.

1. **Correctness.** A cup-with-handle is defined over 25–65 *daily* bars. Running
   the detector on a half-formed intraday bar reads incomplete geometry and
   emits signals that vanish by the close. The detector only ever sees closed
   bars.

2. **Backtest parity (Principle P2).** The backtest runs the detector once per
   day on daily bars. If live ran it per tick, live and backtest would be
   different programs, and every backtest number would stop predicting live
   behaviour. **This is the load-bearing reason.**

3. **Cost.** Detect + score over a large universe takes seconds. Ticks arrive
   faster. Per-tick strategy would fall permanently behind.

## Market direction (M)

The `M` letter asks whether the **market** is trending, not the stock. So it is
a property of the index, computed from the benchmark (`^NSEI`, `^GSPC`).

**M is computed once per day and shared by every symbol's score.** If each
symbol computed M itself, two symbols closing seconds apart could disagree
whenever the index ticked between them. `LiveEngine._refresh_market()` refreshes
exactly once per day and every close into that day reuses it.

## Event-handling guarantees

Streams misbehave: they deliver duplicates, out-of-order events, and stale
stamps. `RollingWindow` is defensive by construction:

| Guarantee | Mechanism |
|---|---|
| **idempotent** | a `(symbol, ts)` already seen is counted and ignored |
| **ordered** | an older event is inserted in place, not appended |
| **bounded** | the window holds at most `capacity` events (`deque(maxlen=)`) |
| **monotonic** | a bar closes once and never un-closes |

Duplicates and out-of-order arrivals are **counted** (`window.duplicates`,
`window.out_of_order`), so a misbehaving source is visible rather than silent.

## Indicators

Indicators are small `update(window, event) -> float | None` functions that
recompute only the tail of the window. Shipped: `sma`, `volume_ratio`,
`pct_from_high`, `rate_of_change`.

**A failing indicator records its error and the others keep computing**
(Principle P6). One broken computation never stops the stream.

## The one bridge

`RollingWindow.to_bars()` converts the window into bar dicts, which
`LiveEngine._window_bars()` turns into `Bar` objects for the batch detector.
**That is the only output that matters** — the stream's whole job is to fill a
window the normal engine can read.

## Verified

Parity is tested: a stream built from RELIANCE.NS daily bars produced **the same
grade** as a batch score of the same window (`P2 PARITY: OK`). This is the check
that keeps the design honest.

## What is missing

- **a polling feeder** that turns provider quotes into `StreamEvent`s
- **the bar-close → watchlist → confirm → recommend chain** (ENG-5)
- **async handlers.** `on_bar_close` runs handlers inline, so a slow handler
  slows ingestion. Real deployments push onto a queue.
