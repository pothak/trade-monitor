# Backlog

The authoritative list of changes we intend to make. One row per change.

## How to keep track

**IDs are permanent.** `AREA-N`. Never renumber, never reuse. A dropped item
keeps its ID and gets status `DROPPED` so the history stays readable.

**Status** is one of:

| Status | Meaning |
|---|---|
| `TODO` | Agreed, not started |
| `WIP` | Being worked on right now |
| `BLOCKED` | Cannot start until `Depends` clears |
| `DONE` | Complete and verified |
| `DROPPED` | No longer wanted, kept for history |

**Rules**

1. Only one item is `WIP` at a time.
2. `DONE` requires the `DoD` (definition of done) to be true, not just "code
   written". If it is not verifiable yet, the item stays `WIP`.
3. When a new change is noticed, add a row immediately. Do not hold it in chat.
4. When an item finishes, append a line to [Change log](#change-log) at the
   bottom. The log is append-only.
5. `Depends` names other IDs. An item with an open dependency is `BLOCKED`.

**Sizes** — `S` under an hour, `M` a session, `L` multi-session / needs design.

---

## Tracked work

### ENG — engine / orchestration

| ID | Change | Why | Depends | Size | Status |
|---|---|---|---|---|---|
| ENG-1 | Scan orchestrator that runs `plan -> fetch -> detect -> score -> thesis -> store` as one call | No module ties the built pieces together; today each step is called by hand | — | M | TODO |
| ENG-2 | Config object + loader (file + env), validated against `PluginSpec.config_schema` | Config must select plugins by name; nothing else may hardcode an implementation | INFRA-4 | M | TODO |
| ENG-3 | Run-level parallelism + resume (a scan that can be interrupted and continued) | Full-universe scans are ~2500 symbols and will be interrupted | ENG-1 | M | TODO |
| ENG-4 | Scheduling for recurring scans (daily close / weekly) | The product is a scanner, not a one-shot script | ENG-1 | S | TODO |
| ENG-5 | Monitor loop: decide (watch/enter/skip) -> position -> outcome -> calibration | The app is a **trade monitor**; the engine currently stops at "produce theses". Store tables `decisions`/`outcomes` and `Store.calibration()` exist but nothing drives them | ENG-1 | L | TODO |
| ENG-6 | Instrument per-stage latency (fetch vs detect vs fundamentals) | Latency is a stated design constraint but is currently only inferred from total wall time | ENG-1 | S | TODO |

### DATA — data feeds

| ID | Change | Why | Depends | Size | Status |
|---|---|---|---|---|---|
| DATA-1 | Fix `NSE_LIST_URL` in `universe.py` | Domain is `archives.nsiindia.com`; the real NSE archive host is `nsearchives.nseindia.com`. The URL 404s, so `load_nse` silently falls back to the 48-symbol `FALLBACK_NSE`. This is why a "full NSE scan" returned 48 symbols | — | S | TODO |
| DATA-2 | Full NSE listing loader (`EQUITY_L.csv`), not the NIFTY 50 constituent file | Current source is a 50-name index file, so tier 2 can never hold the full listing | DATA-1 | S | TODO |
| DATA-3 | `NseMcpFeed` — DataFeed wrapping the official NSE MCP (`mcp.nseindia.in`) | Official source for corporate actions, market breadth, symbol lookup and 50-at-a-time snapshots. No cookie forging, no ToS workaround | DATA-4 | M | BLOCKED |
| DATA-4 | Determine whether the NSE MCP `2023` gap is permanent or a cold-tier issue | Verified: every 2023 date returns 0 rows across symbols with retries. If permanent, the MCP cannot be the price-history source | — | S | TODO |
| DATA-5 | India `I` letter data — NSE FII/DPIIT shareholding | `institutional_holders` returns 0 rows for every `.NS` ticker, so `I` scores `unknown` for all of India | — | M | TODO |
| DATA-6 | Point-in-time fundamentals (EPS as reported on date D) | Backtest scores C/A with as-of-now EPS, which is look-ahead. Weakest part of the method | — | L | TODO |
| DATA-7 | Split/bonus adjustment on price series | Unadjusted prices break cup geometry and YoY comparisons across corporate actions | DATA-3 | M | TODO |
| DATA-8 | On-disk cache for prices and fundamentals | Re-fetching the same series makes iteration and backtests slow and rate-limit-prone | — | S | TODO |
| DATA-9 | Remove `.T/.HK/.KS/.TWO` suffix handling from `_suffix` | Inherited from the rejected Chinese plugin's market set; only `IN`/`US` are supported | — | S | TODO |

### PAT — patterns

| ID | Change | Why | Depends | Size | Status |
|---|---|---|---|---|---|
| PAT-1 | Characterise detector selectivity (fires ~6 of 25 windows) | Too selective means too few signals to trade; too loose means noise. Needs a measured answer, not a guess | BT-6 | M | TODO |
| PAT-2 | Validate `cup_depth` / `cup_duration` / `handle_*` criteria against real winners | Current thresholds are transcription, not measured | BT-3 | M | TODO |
| PAT-3 | Promote volume dry-up from a strength tuner to an optional hard gate | O'Neil treats dry-up in the handle as structural, not a bonus | — | S | TODO |
| PAT-4 | More patterns: flat base, double bottom, high-tight flag | One pattern is a proof, not a product; registry already supports adding them | PAT-5 | L | TODO |
| PAT-5 | Pattern detector tests on fixtures | `fixtures/` and `tests/` are empty; the detector has no regression net | INFRA-3 | M | TODO |
| PAT-6 | Fix handle anchoring for a still-forming base | Validated defect (V-1): on a forming base, `recovery_idx` is None so `handle_start_idx` falls through to `base_idx + 1`, anchoring the handle at the low. Result: `cup_bars` collapses (10 vs min 25) and `retrace` exceeds 100% (141%). The detector fires 15x over 400 bars of history but **never on the latest bar** — it can only see completed bases | — | M | TODO |

### STRAT — strategy

| ID | Change | Why | Depends | Size | Status |
|---|---|---|---|---|---|
| STRAT-1 | Make the M-gate grade cap configurable | `min(base, 54.0)` is hardcoded in `can_slim.py` | ENG-2 | S | TODO |
| STRAT-2 | Narrative must state when `I` is `unknown` and why | A thesis that silently omits a letter looks complete when it is not | DATA-5 | S | TODO |
| STRAT-3 | Move weights and grade cutoffs out of constants into config | "Configurable strategies" is a stated requirement | ENG-2 | S | TODO |
| STRAT-4 | Second strategy registered alongside CAN SLIM | Proves the `Strategy` seam actually holds | STRAT-3, ENG-2 | L | TODO |
| STRAT-5 | Scorer unit tests, especially `_match_year_ago` fiscal matching | India quarters have gaps (`SBIN.NS` nan, `RELIANCE` missing 2025-09-30); matching is easy to get subtly wrong | INFRA-3 | M | TODO |

### VAL — validation

| ID | Change | Why | Depends | Size | Status |
|---|---|---|---|---|---|
| VAL-1 | Rule-validator base + first rule validators | The `Validator` protocol exists; nothing implements it | ENG-1 | M | TODO |
| VAL-2 | Model (LLM) validator adapter | The stated design allows a model to challenge evidence and adjust confidence | VAL-1 | M | TODO |
| VAL-3 | Enforce in the engine that a model cannot override a failing rule validator | This is a safety invariant written into the contract; it must be tested, not assumed | VAL-1, ENG-1 | S | TODO |
| VAL-4 | Persist `ValidationResult` and surface it on the thesis | Provenance must be auditable after the fact | VAL-1, STORE-1 | S | TODO |

### DEL — delivery

| ID | Change | Why | Depends | Size | Status |
|---|---|---|---|---|---|
| DEL-1 | Delivery registry + base adapter | `delivery/` is empty; delivery is a stated requirement | INFRA-4 | S | TODO |
| DEL-2 | Console / markdown-file delivery (dev sink) | Fastest way to see a thesis end-to-end without external services | DEL-1 | S | TODO |
| DEL-3 | Telegram adapter | Enabled in openclaw but unconfigured; needs bot token and chat id | DEL-1 | S | BLOCKED |
| DEL-4 | HTTP/web delivery backing the viewer | The viewer is the chosen primary surface | DEL-1, UI-1 | M | TODO |
| DEL-5 | MCP delivery — expose theses to OpenClaw as MCP tools | This is where MCP is the right shape, and it makes the Android shell useful | ENG-1 | M | TODO |

### RENDER — rendering

| ID | Change | Why | Depends | Size | Status |
|---|---|---|---|---|---|
| RENDER-1 | Chart renderer: price series + cup/handle + pivot/target/stop overlay | A pattern thesis without a picture is not reviewable | PAT-5 | M | TODO |
| RENDER-2 | Persist rendered charts as `artifacts` rows | Table already exists (`kind = chart`), nothing writes to it | RENDER-1, STORE-1 | S | TODO |
| RENDER-3 | Text/markdown thesis report | Needed for Telegram and for archiving decisions | DEL-2 | S | TODO |

### UI — viewer

| ID | Change | Why | Depends | Size | Status |
|---|---|---|---|---|---|
| UI-1 | `canslim serve` local web server | Chosen viewer over OpenClaw-native | ENG-1 | M | TODO |
| UI-2 | Thesis list + detail views, driven only by the stored thesis | The viewer must never recompute a thesis | UI-1, STORE-1 | M | TODO |
| UI-3 | Surface size/liquidity tier per name | Stated requirement: small-cap vs mega-cap diligence must be visible | UI-2, DATA-2 | M | TODO |
| UI-4 | Mobile-first layout (usable in the OpenClaw Android shell) | The phone is the primary device | UI-2 | M | TODO |

### CLI

| ID | Change | Why | Depends | Size | Status |
|---|---|---|---|---|---|
| CLI-1 | `canslim scan` | No entrypoint exists at all | ENG-1 | M | TODO |
| CLI-2 | `canslim serve` | Wraps UI-1 | UI-1 | S | TODO |
| CLI-3 | `canslim backtest` | Harness exists but is only callable from Python | BT-6 | S | TODO |
| CLI-4 | `canslim plugins` (prints `Registry.describe()` for each kind) | Makes the plugin model inspectable | INFRA-4 | S | TODO |
| CLI-5 | Config file loading and overrides | Pairs with ENG-2 | ENG-2 | S | TODO |

### BT — backtest

| ID | Change | Why | Depends | Size | Status |
|---|---|---|---|---|---|
| BT-1 | Fix the target definition | `target = pivot + cup_depth` demands ~31% within 60 days; target hit rate is structurally 0%. Make pivot-cleared the primary metric and treat the measured move as a band | — | S | TODO |
| BT-2 | Replace `last_close` entry with pivot-triggered entry | Measuring from the signal close assumes a fill that a breakout strategy would not get | — | S | TODO |
| BT-3 | Threshold ablation sweep over `CupWithHandle.DEFAULTS` | Turns a guess into a measured setting | BT-6 | M | TODO |
| BT-4 | Non-overlapping windows + confidence intervals | Current sample is small and windows overlap, which inflates apparent significance | BT-1 | M | TODO |
| BT-5 | Stronger baseline (same market, matched period) | `+2.59%` baseline currently comes from the same symbol only | BT-4 | S | TODO |
| BT-6 | Multi-symbol portfolio-level backtest | Per-symbol runs cannot answer "is the detector viable" | BT-4, DATA-8 | M | TODO |
| BT-7 | Write the viability verdict (proceed / retune / abandon) | The whole point of "backtest first" is a decision, not a number | BT-1..BT-6 | S | TODO |

### STORE

| ID | Change | Why | Depends | Size | Status |
|---|---|---|---|---|---|
| STORE-1 | Schema versioning + migrations | Adding columns to a live DB with no migration path will hurt | — | S | TODO |
| STORE-2 | Retention/pruning for observations | A daily full-universe scan writes a row per symbol per run | ENG-4 | S | TODO |

### INFRA — repo

| ID | Change | Why | Depends | Size | Status |
|---|---|---|---|---|---|
| INFRA-1 | `git init` + first commit | The project is 2587 LOC of unversioned work | — | S | TODO |
| INFRA-2 | `pyproject.toml` with pinned deps | No packaging or lockfile; `.venv` is the only record | — | S | TODO |
| INFRA-3 | pytest setup + first fixtures | `tests/` and `fixtures/` are empty; nothing is guarded by CI | INFRA-1 | M | TODO |
| INFRA-4 | Unify the registry: `datafeed` uses `registry.Registry`, `patterns`/`strategy` use plain dicts | Two shapes for the same concept; the plain-dict ones have no `describe()`, no `kind`, no `config_schema` | — | S | TODO |
| INFRA-5 | Add `__init__.py` to `validate/`, `delivery/`, `render/` | They are empty namespace dirs; imports would fail | — | S | TODO |
| INFRA-6 | Lint/format config (ruff) | No style gate exists | INFRA-2 | S | TODO |
| INFRA-7 | REQUIREMENTS / constraints doc for the data sources | Terms-of-use (NSE MCP is "educational and research") and rate limits need to be written down | DATA-3, DATA-4 | S | TODO |

---

## Change log

Append-only. Newest last.

| Date | ID | Note |
|---|---|---|
| 2026-10-20 | — | Backlog created from the as-built review. |
| 2026-10-20 | ENG-1 | Engine built: `canslim/engine.py`, cheap-filter-first ordered. Registries unified. `validate/`, `delivery/`, `render/` given `__init__.py`. |
| 2026-10-20 | PAT-6, ENG-5, ENG-6 | V-1 validation run. Engine PASSES. Detector defect found and characterised: fires 15x over history, 0x on the latest bar. Monitor loop and latency instrumentation added as new items. |
