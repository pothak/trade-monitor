# Roadmap

**Status** living document.

Ordered by dependency, not by appeal. Each phase says what "done" means, so
progress is checkable rather than felt.

---

## Phase 0 — Foundation ✅ DONE

- [x] contracts, registry, plugin seam
- [x] yfinance feed
- [x] cup-with-handle detector
- [x] CAN SLIM reference strategy
- [x] batch engine with cheap-filter-first gating
- [x] append-only ledger
- [x] point-in-time `MarketStore` (`as_of` enforced by construction)
- [x] streaming windows + indicators + bar-close strategy wiring
- [x] live/batch parity verified
- [x] rename to `trade-monitor`; contracts extracted to their own package
- [x] `install.sh`, CLI, JSON API, no-build dashboard
- [x] MIT license, dependency policy, three free reference strategies

**Done when:** a stranger can clone, install, run a scan and see it in a browser.
✅

---

## Phase 1 — Trust: make the record verifiable

The whole promise rests on "the record is not manipulated". Today the ledger is
append-only but **not provable**.

- [ ] **hash-chain the ledger** (R2) — each event embeds the previous hash
- [ ] **content-hash cited data** — a claim is checkable against the stored bar
- [ ] **verifiable replay** — re-run from the ledger, get the same decisions
- [ ] `trade-monitor ledger --verify` turns green

**Done when:** editing any historical event makes `--verify` fail, and replay
reproduces a past decision byte for byte.

---

## Phase 2 — Make the monitor actually monitor

Right now nothing watches anything over time.

- [ ] **PAT-6**: fix handle anchoring so a still-forming base is recognised
- [ ] **ENG-5 monitor loop**: watchlist promotion → 1–2 day re-confirmation →
      recommendation → outcome → calibration, each writing ledger events
- [ ] **polling feeder**: provider quotes → `StreamEvent`s
- [ ] measurement, not guesswork: per-stage latency (ENG-6)

**Done when:** run it daily and it promotes, confirms across two days, issues or
withholds a recommendation, and writes every step to the ledger.

---

## Phase 3 — Trustworthy numbers

The backtest currently cannot be believed (BT-1..BT-7).

- [ ] reachable target metric
- [ ] non-overlapping windows
- [ ] point-in-time fundamentals
- [ ] calibration surfaced in the dashboard

**Done when:** a backtest number has a defensible meaning and a stated error bar.

---

## Phase 4 — Data breadth

- [ ] **DATA-1/2**: fix the NSE universe URL (currently 48 symbols, not 500)
- [ ] **DATA-5**: India institutional sponsorship (the `I` letter)
- [ ] NSE MCP as a second feed behind the same interface
- [ ] trading-calendar correctness (exchange holidays)
- [ ] size / liquidity tier surfaced in the UI

**Done when:** the scan covers the real universe, and gaps are labelled in the
UI rather than silently absent.

---

## Phase 5 — Usability

- [ ] charts in the dashboard (uPlot, MIT, vendored)
- [ ] watchlist and backtest views
- [ ] data-quality view
- [ ] one-command daily scheduling
- [ ] delivery adapters (the `delivery/` registry is empty)

---

## Never (non-goals)

- placing orders — the user acts, not the app
- multi-user / accounts / tenancy
- a paid service in the critical path
- any dependency with a copyleft or source-available license
