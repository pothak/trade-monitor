# Architecture

As-built. If this drifts from the code, the code wins and this file should be
updated in the same change.

## The one rule

The engine talks only to the Protocols in `contracts.py`. A data source, a
pattern, a strategy, a validator or a delivery target can be added or swapped
without editing engine code. OpenClaw is a consumer of this package, never a
dependency of it.

## Layers

```
                          ┌──────────────────────────────────────┐
                          │            CONTRACTS                 │  contracts.py
                          │   (the only thing the engine knows)   │  [built]
                          │                                      │
                          │  DataFeed      PatternDetector        │
                          │  Strategy      Validator              │
                          │  DeliveryAdapter                      │
                          │                                      │
                          │  Bar · PriceSeries · QuarterEPS       │
                          │  Fundamentals · Evidence · Thesis     │
                          │  Invalidation · ValidationResult      │
                          └───────────────┬──────────────────────┘
                                          │ implemented by
                    ┌─────────────────────┼──────────────────────┐
                    │                     │                      │
              ┌─────▼──────┐     ┌────────▼───────┐    ┌─────────▼──────┐
              │  DataFeed   │     │ PatternDetector│    │   Strategy     │
              ├─────────────┤     ├────────────────┤    ├────────────────┤
              │ [x]yfinance │     │ [x]cup_with_   │    │ [x]canslim     │
              │ [ ]nse_mcp  │     │      handle    │    │ [ ](second)    │
              └─────────────┘     └────────────────┘    └────────────────┘

  registry.py  PluginSpec(name, kind, factory, requires, provides, config_schema)
               NAME -> FACTORY per plugin kind. Nothing hardcodes an impl.
```

## Scan pipeline

```
  load_nse / load_sp500 / read_symbols            universe.py
        │
        ▼
  build_plan() ──► ScanPlan{watchlist, liquid, extended}
        │              tier 0/1/2, dedup, watchlist wins
        ▼
  scan_prices(symbols, fetch, workers, RateLimiter)   jittered token bucket
        │  ThreadPoolExecutor, a failed symbol is dropped, never fatal
        ▼
  {symbol: PriceSeries}
        │
        ▼
  ┌────────────────┐  geom dict     ┌──────────────────┐  Evidencex7, Grade, conf
  │ CupWithHandle  │ ─────────────► │  CanSlimStrategy │ ────────────────┐
  │  .detect(ps)   │ pivot, target  │  .score(...)     │                 │
  └────────────────┘ stop, strength └──────────────────┘                 │
        ▲                                        ▲                        │
        │                                        │                        ▼
  DEFAULTS                              LETTERS: C A N S L M I      build_thesis()
  (cup_depth, cup_duration,             score_c/a/n/s/l/m/i        │
   handle_duration, handle_*)                                       ▼
                                                          ┌─────────────────────┐
                                                          │       Thesis        │
                                                          │ id = sha256(symbol| │
                                                          │  strategy|as_of|    │
                                                          │  grade|confidence)  │
                                                          │ evidence[]          │
                                                          │ invalidation        │
                                                          │ measured_move_target│
                                                          └──────────┬──────────┘
                                                                     │
                          ┌──────────────────────────────────────────┤
                          ▼                                          ▼
                ┌───────────────────┐                    ┌────────────────────┐
                │  Validator  [ ]   │                    │  Store (sqlite) [x]│
                │  rule | model     │                    │  runs observations │
                │  model may adjust │                    │  theses decisions  │
                │  confidence, may  │                    │  outcomes artifacts│
                │  not override a   │                    └────────────────────┘
                │  failing rule     │
                └─────────┬─────────┘
                          ▼
                ┌───────────────────┐
                │ DeliveryAdapter[ ]│
                │ telegram/web/mcp  │
                └─────────┬─────────┘
                          ▼
                ┌───────────────────┐
                │  render/  [ ]     │  charts, reports
                └───────────────────┘
```

## Tiering, and why it exists

Scanning all of NSE plus the S&P 500 is viable, but not as one flat pass. Yahoo
rate-limits aggressive parallel fetching, so the universe is cut into tiers that
trade coverage for latency:

| Tier | Name | Contents | Treatment |
|---|---|---|---|
| 0 | watchlist | explicit symbols | always scanned |
| 1 | liquid | index members | full fundamentals (EPS, float) |
| 2 | extended | full listing | price + pattern only, no fundamentals |

Tier 2 is what makes full-universe coverage affordable: price data is one
request and pattern detection needs nothing else. Fundamentals, the expensive
part, are fetched only for names that already show a pattern.

## Contracts reference

### DataFeed
```python
name: str
price_series(symbol, days) -> PriceSeries | None
fundamentals(symbol)          -> Fundamentals | None
index_for(market)             -> str | None   # benchmark for L and M
```

### PatternDetector
```python
name: str
detect(series, **options) -> dict | None   # geometry, or None
```

### Strategy
```python
name: str
requires: tuple[str, ...]                  # pattern names it needs
score(series, fundamentals, **options) -> (list[Evidence], Grade, int)
narrative(thesis) -> str
```

### Validator
```python
name: str
kind: "rule" | "model"                     # declared, so provenance is auditable
validate(thesis) -> ValidationResult
```
`ValidationResult.confidence_delta` is additive and clamped by the engine.
`blocking=True` means a `False` verdict rejects the thesis. A model validator
may never override a failing rule validator.

### DeliveryAdapter
```python
name: str
deliver(thesis, **options) -> bool         # never fetches or recomputes
```

## Grading, inside `CanSlimStrategy`

```
base        = 100 * (weight passed) / (weight available)
pattern     = base + pattern_strength * pattern_bonus_weight   (capped at 100)
M gate      = if M fails, base = min(base, 54)   -> grade capped at C
grade       = A>=85  B>=70  C>=55  D>=40  else F
confidence  = base * (0.55 + 0.45 * coverage)    -> 0..100
```

Unknown letters are removed from the denominator rather than counted as
failures, so they lower *confidence* (via coverage) instead of the grade.

Default weights: `C = 1.5`, all others `1.0`.

## Persistence

SQLite in WAL mode. Tables:

| Table | Key | Holds |
|---|---|---|
| `runs` | `id` | one scan: counters, market, strategy, config |
| `observations` | `(run_id, symbol)` | every symbol seen, hits *and* misses |
| `theses` | `id` | the rendered thesis payload |
| `decisions` | `id` | approve / reject, with a snapshot |
| `outcomes` | `thesis_id` | forward result after the horizon |
| `artifacts` | `(thesis_id, kind)` | chart / report paths |

Recording misses matters: `observation_history`, `first_seen` and `seen_count`
are what make "this name has been on the list for 30 days" answerable.

## Deliberate design choices

- **Thesis is immutable.** Downstream only renders it. Nothing recomputes.
- **Provenance is carried, not inferred.** `Fundamentals.degraded` and
  `Thesis.degraded_inputs` record what the provider did not return.
- **Look-ahead is guarded at one point.** `backtest.truncate()` is the single
  place where time is cut. Everything downstream trusts it.
- **Failures never crash a scan.** A broken letter becomes an `unknown` with the
  reason attached; a failed symbol is dropped.

## Known gaps

These are tracked in [BACKLOG.md](BACKLOG.md). The short version:

- No orchestrator. The pipeline above is steps you currently call by hand (ENG-1).
- `datafeed` uses `registry.Registry`; `patterns` and `strategy` use plain dicts
  (INFRA-4).
- `validate/`, `delivery/`, `render/` are empty directories with no
  `__init__.py` (INFRA-5).
- `NSE_LIST_URL` points at a host that does not resolve, so NSE scans silently
  fall back to 48 symbols (DATA-1).
- Backtest target is unreachable by construction, so its target-hit rate is
  meaningless (BT-1).
