# Decisions

Append-only. Decisions are `DEC-N`. Never renumber. A reversed decision keeps
its ID and gets a `Superseded by` line.

## Format

```
DEC-N   <title>
Date    YYYY-MM-DD
Status  Accepted | Open | Superseded by DEC-M
Context what forced a choice
Decision what we chose
Why      the reason, including what we rejected
```

---

### DEC-1 Standalone engine, OpenClaw decoupled

- **Date** 2026-10-20
- **Status** Accepted
- **Context** An existing OpenClaw plugin (`@weaxs/openclaw-stock-analysis`)
  covers A/HK/US/JP/KR/TW with Chinese descriptions, no cup-with-handle and no
  India.
- **Decision** Build a new standalone package. OpenClaw consumes it; it is never
  a dependency.
- **Why** Forking the plugin would inherit its market set, its Chinese copy and
  its lack of a pattern layer. The plugin's own venv is left untouched.

### DEC-2 Cup-with-handle is the first pattern

- **Date** 2026-10-20
- **Status** Accepted
- **Decision** Implement `cup_with_handle` first, behind a `PatternDetector`
  Protocol.
- **Why** It is the canonical CAN SLIM base. Building it behind the Protocol
  proves the seam before more patterns are added (PAT-4).

### DEC-3 CAN SLIM is the first strategy, and it is configurable

- **Date** 2026-10-20
- **Status** Accepted
- **Decision** Score C A N S L M I, with configurable weights and cutoffs, and
  route the result through the `Strategy` Protocol.
- **Why** Others must be addable without editing the engine (STRAT-4).

### DEC-4 `I` covers both US and India, via one interface

- **Date** 2026-10-20
- **Status** Accepted, interpretation still to confirm
- **Context** "Have both US and India stock markets" was read as `I` must be
  populated for both.
- **Decision** US via `institutional_holders`; India via an NSE FII/DII
  shareholding source; both behind one interface.
- **Why** `institutional_holders` returns 0 rows for every `.NS` ticker, so a
  US-only implementation would leave `I` `unknown` for all of India.
- **Open** The interpretation was flagged, not re-confirmed. India shareholding
  is quarterly and coarse next to 13F-style data, so the two are not the same
  quality. See DATA-5.

### DEC-5 Viewer is a local web server + browser UI

- **Date** 2026-10-20
- **Status** Accepted
- **Decision** `canslim serve`, local HTTP, browser UI. OpenClaw on Android is a
  shell that points at it.
- **Why** Rejected a native OpenClaw extension: it would couple the viewer to
  the shell and stop it being independently usable.

### DEC-6 Backtest before build-out

- **Date** 2026-10-20
- **Status** Accepted
- **Decision** Validate the detector before investing in charts, UI or delivery.
- **Why** If the detector has no edge, everything downstream is wasted work.
  Deliverable is BT-7, a written verdict.

### DEC-7 No restrictive watchlist; scan the whole universe

- **Date** 2026-10-20
- **Status** Accepted
- **Decision** Scan all NSE and the S&P 500, tiered by cost (see tiering in
  ARCHITECTURE.md).
- **Why** A watchlist hides the discovery problem the app exists to solve. Cost
  is managed by tiering rather than by narrowing the universe.

### DEC-8 Size/liquidity tier must be surfaced in the UI

- **Date** 2026-10-20
- **Status** Accepted
- **Decision** Every thesis carries a size/liquidity tier shown in the viewer.
- **Why** A 33% move in a small-cap and in a mega-cap are different claims.
  Surfacing the tier is the due-diligence affordance. Tracked as UI-3.

### DEC-9 NSE MCP is not the price-history source

- **Date** 2026-10-20
- **Status** Accepted, pending DATA-4
- **Context** The official NSE MCP (`mcp.nseindia.in/bhavcopy/cm/mcp`) was
  measured directly. It is fast (0.09-0.17s/call, 8 parallel sessions clean) but
  every 2023 date returns 0 rows, across symbols and retries. `months` is capped
  at 3, so full history requires paging.
- **Decision** Do not use it for price history. Use it where it is better than
  yfinance: corporate actions, market breadth, official symbol lookup, and
  50-at-a-time snapshots. Keep yfinance for history.
- **Why** A 12-month hole disqualifies it for backtesting, which is the current
  priority. Confirming whether the hole is permanent is DATA-4. The MCP also
  carries an "educational and research purposes" disclaimer, recorded in
  INFRA-7.

### DEC-10 MCP is an agent surface, not a data bus

- **Date** 2026-10-20
- **Status** Accepted
- **Decision** Where MCP is used, it is used to expose *our* output to an LLM
  agent (DEL-5), not to move bulk data into the engine.
- **Why** MCP is a tool-calling protocol for LLMs. A 2500-symbol batch scan is
  the wrong shape for it. Bulk data goes through `DataFeed` implementations.

---

## Open questions

| # | Question | Blocks |
|---|---|---|
| Q1 | Is the NSE MCP 2023 gap permanent? | DATA-3, DEC-9 |
| Q2 | Backtest scope: NIFTY 50 + S&P 100, or wider? | BT-6 |
| Q3 | Backtest horizon: 40, 60 or 120 days? | BT-1 |
| Q4 | Primary backtest metric: pivot-cleared? | BT-1 |
| Q5 | Telegram bot token and chat id | DEL-3 |
| Q6 | India `I` quality acceptable as a coarse quarterly proxy? | DEC-4 |
