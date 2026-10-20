# Install

**Status** BUILT — `install.sh` works end to end and is idempotent.

## The goal

A stranger clones the repo, runs **one command**, and has a working monitor.
No account, no API key, no subscription, no Node, no Docker.

```bash
git clone https://github.com/pothak/trade-monitor
cd trade-monitor
./install.sh
```

## What `install.sh` does

| Step | Action | Failure behaviour |
|---|---|---|
| 1 | find Python ≥ 3.10 | fail with a clear message |
| 2 | create `.venv` (never touches system Python) | fail with a hint for Debian users |
| 3 | install `trade-monitor-contracts` then the engine, editable | fail |
| 4 | **verify the license policy** (permissive only) | fail if a disallowed license appears |
| 5 | verify imports and the strategy registry loads `canslim` | fail |
| 6 | optionally seed market data (`SEED=N`) | warn and continue (free data is imperfect) |
| 7 | print next steps | — |

**Re-running is safe.** It reuses the venv and reinstalls cleanly.

## Options

```bash
./install.sh              # install, no data
SEED=25 ./install.sh      # install and seed 25 symbols
PORT=9000 ./install.sh    # change the printed dashboard port
```

## After install

```bash
trade-monitor serve               # dashboard at http://127.0.0.1:8787
trade-monitor scan --strategy canslim --market IN
trade-monitor strategies          # list what is loaded
trade-monitor seed --limit 50     # pull data for the dashboard
trade-monitor ledger --limit 30   # the audit trail
```

## Where things live

```
~/.trade-monitor/
  engine.db     runs, observations, theses, decisions, outcomes
  market.db     normalized price + EPS data
  ledger.db     the append-only audit trail
  strategies/   your extra strategies (auto-loaded)
```

`./strategies/` (in the repo) is also scanned, plus
`~/.config/trade-monitor/strategies/`.

## Adding your own strategy

```bash
# 1. write strategies/my_idea.py   (see strategies/momentum_rank.py)
# 2. it is picked up automatically:
trade-monitor strategies
trade-monitor scan --strategy my_idea --market IN
```

No rebuild. No registration step. See [PLUGINS.md](PLUGINS.md).

## Dependencies

The engine core is **stdlib-only**. The installed edge dependencies are
`yfinance`, `pandas`, `requests` — all permissive licenses, verified at install
time. See [DEPENDENCIES.md](DEPENDENCIES.md) and [NOTICE.md](../NOTICE.md).

## Requirements

- **Python 3.10+** (3.12 or newer recommended)
- ~200 MB disk for the venv; data grows with coverage
- No GPU, no database server, no network service other than the data sources
