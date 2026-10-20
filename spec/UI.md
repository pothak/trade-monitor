# Dashboard

**Status** PARTIAL — a working no-build dashboard and JSON API exist. Charts,
live streaming updates, the monitor loop view, and backtest view are not built.

## What it is

A local web page, served by the app itself, showing what the monitor is doing:
the theses it produced, the runs it completed, the audit trail, and the quality
of the data underneath.

`trade-monitor serve` opens it at `http://127.0.0.1:8787`.

## Why it does not need a build step

The dashboard must obey [DEPENDENCIES.md](DEPENDENCIES.md) and the "usable by
anyone" requirement. A Node/npm build is a **toolchain gate**: a user without
Node could not run the app from source.

So the shipped dashboard is **server-rendered HTML + vanilla JS**, served by the
Python stdlib (`http.server`), with **no npm, no build, no bundler**. It runs the
moment `pip install` finishes.

**The JSON API is the contract.** Because the frontend is just a consumer of the
API, a richer frontend (including a React app) can be added later **against the
same endpoints**, without touching the engine. The API is designed for that.

## The API

| Method | Path | Returns |
|---|---|---|
| GET | `/api/health` | version, db paths, data coverage |
| GET | `/api/strategies` | loaded strategies and their needs |
| GET | `/api/theses?market=&min_grade=&limit=` | stored theses |
| GET | `/api/thesis/<id>` | one thesis + its ledger timeline |
| GET | `/api/runs?limit=` | recent scans |
| GET | `/api/ledger?limit=` | tail of the audit trail |
| GET | `/api/coverage` | what the market store holds |
| POST | `/api/scan` | run a scan (`{strategies, market, symbols, limit}`) |

All verified returning `200`.

## What the current page shows

```
┌───────────────────────────────────────────┬───────────────────────┐
│ Theses                                    │ Audit trail           │
│  [B] TCS.NS   grade B · conf 67%          │  detect.miss ...      │
│  [F] ITC.NS   grade F · conf 0%           │  score.computed ...   │
│      ⚠ data: fundamentals unavailable     │  thesis.created ...   │
│                                           │  run.finished ...     │
│ Recent runs                               │                       │
│  strategy · market · scanned · hits       │  (click a thesis to  │
│                                           │   see its timeline)   │
│ Data coverage                             │                       │
│  symbols · bars · from · to · db          │                       │
└───────────────────────────────────────────┴───────────────────────┘
```

Note the **data-quality warning** on the card (Principle R2 /
[PHILOSOPHY.md](PHILOSOPHY.md)): the UI is where "this number rests on thin
data" becomes visible to the user.

## Planned

| View | Shows | Needs |
|---|---|---|
| **Chart** | price, the detected pattern, indicators | `render/` + uPlot (MIT, vendored) |
| **Watchlist** | names being confirmed over 1–2 days | monitor loop (ENG-5) |
| **Backtest** | walk-forward equity curve + calibration | BT-1..BT-7 fixes |
| **Quality** | per-source coverage and freshness | `fetch_log` surfacing |
| **Live** | streaming indicator values | polling feeder |

## Charts

**uPlot** (MIT, ~40 KB, zero dependencies), **vendored locally** so nothing is
fetched from a CDN at runtime and the app works fully offline. This matches the
"no subscription, no external calls" requirement.

## Server design notes

- **stdlib only** — `ThreadingHTTPServer` + `json`. No framework dependency.
- **lazy stores** — the server starts instantly, even before any scan has
  created the databases.
- **scans run on a background thread** so a request returns at once.
- **never crashes on a bad request** — every handler is wrapped; an error
  becomes a JSON `{"error": ...}`, not a dead server.
- **path traversal is blocked** in static file serving.
