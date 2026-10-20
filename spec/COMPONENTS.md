# Components

**Status** PARTIAL — each component says how much exists.

For each part: what it is, what it must do, and what it must not.

---

## Package layout

```
trade-monitor/
├── trade_monitor_contracts/     the plugin interface (separate, zero deps)
├── trade_monitor/               the engine
│   ├── contracts              -> re-exported from the contracts package
│   ├── registry.py            generic name -> factory registry
│   ├── engine.py              batch scan orchestration
│   ├── stream.py              rolling windows + incremental indicators
│   ├── live.py               stream -> bar close -> strategy
│   ├── universe.py            symbol lists, tiers, rate limiting
│   ├── backtest.py            walk-forward harness
│   ├── cli.py                 command line
│   ├── server/                dashboard: JSON API + static UI
│   ├── datafeed/              DataFeed implementations (yfinance)
│   ├── patterns/              PatternDetector implementations (cup-with-handle)
│   ├── strategy/              Strategy implementations (CAN SLIM) + loader
│   ├── store/                 persistence (engine state, market data, ledger)
│   ├── validate/              Validator registry (empty)
│   ├── delivery/              DeliveryAdapter registry (empty)
│   └── render/                chart registry (empty)
├── strategies/                  ★ user strategies, scanned at runtime
├── spec/                        this folder
└── docs/                        as-built + history
```

---

## contracts — the boundary

**Status** BUILT

Defines the Protocols and data shapes every other part agrees on, and lives in a
**separate package** so a strategy author depends on the interface, not the app.

*Must.* Be dependency-free. Be stable. Never import the engine.

*Must not.* Contain logic. Know about storage, HTTP, or providers.

See [PLUGINS.md](PLUGINS.md).

---

## registry — the plugin seam

**Status** BUILT

A generic `name -> factory` map. Every plugin kind (feed, pattern, strategy,
validator, delivery, render) has one.

*Must.* Allow registration by name and creation on demand.

*Must not.* Know what it is registering.

---

## engine — batch scan

**Status** BUILT

Runs one pass: plan → prices → detect → **gate** → fundamentals → score →
thesis → store + ledger.

*Must.* Order work cheapest-first. Gate the expensive step. Record misses as
well as hits. Never let one symbol abort the pass. Write audit events.

*Must not.* Assume any particular strategy shape.

Key rule: a strategy that declares **no** pattern requirement is not gated by
detection — it scores every name.

---

## stream + live — the streaming path

**Status** PARTIAL — compute and bar-close wiring built; a polling feeder that
emits events is **not built**.

`stream.py` maintains per-symbol rolling windows (idempotent, ordered, bounded)
and updates indicators incrementally. `live.py` turns a bar close into a
strategy decision, with market direction (M) computed once per day.

*Must.* Never decide per tick. Keep live and batch on one code path.

See [STREAMING.md](STREAMING.md).

---

## universe — symbols and pacing

**Status** PARTIAL — loaders and rate limiting built; the NSE list URL is wrong
so `load_nse` falls back to 48 symbols (backlog DATA-1).

*Must.* Load symbol lists, dedupe across tiers, pace fetches.

*Must not.* Fetch prices itself; that is the feed's job.

---

## backtest — walk-forward harness

**Status** PARTIAL — runs, but the target metric is unreachable and windows
overlap (backlog BT-1..BT-7). Numbers are not yet trustworthy.

*Must.* Truncate history so no decision sees the future.

---

## store — persistence

**Status** PARTIAL — engine state and ledger built; the hash chain that makes
the ledger *verifiable* is **not built** (Principle R2).

Three stores, one job each: `Store` (engine state), `MarketStore` (normalized,
point-in-time market data), `Ledger` (append-only audit trail).

See [DATA-MODEL.md](DATA-MODEL.md).

---

## datafeed — providers

**Status** PARTIAL — yfinance built. NSE MCP and India institutional data
(DATA-5) not built.

Each provider maps a source into the normalized shapes and reports what it
could not get via `degraded`.

---

## patterns — detectors

**Status** PARTIAL — cup-with-handle built, but it cannot recognise a
still-forming base (PAT-6), so the monitor finds nothing to monitor.

---

## strategy — scoring

**Status** PARTIAL — CAN SLIM built; the re-confirmation lifecycle over 1–2 days
(ENG-5) is not built.

---

## loader — runtime injection

**Status** BUILT

Scans directories for `.py` files, loads `STRATEGY` / `STRATEGIES` /
`register(registry)`, fault-isolated, logging every load or skip to the ledger.

---

## cli

**Status** BUILT — `scan`, `serve`, `strategies`, `seed`, `ledger`, `version`.

---

## server — the dashboard

**Status** PARTIAL — JSON API and a no-build UI built; charts, live updates and
the bar-close monitor loop are not.

See [UI.md](UI.md).

---

## Not built

`validate/`, `delivery/`, `render/` are declared registries with no
implementations. The monitor loop (ENG-5), the hash-chained ledger (R2), and the
size/liquidity tier are absent. See [ROADMAP.md](ROADMAP.md).
