# canslim

A standalone stock-analysis engine. Scans the NSE and S&P 500 universe, detects
chart patterns (cup-with-handle first), scores them with a configurable CAN SLIM
strategy, stores the resulting theses in a local database, and delivers them
through pluggable channels.

OpenClaw is a *consumer* of this package, never a dependency. The Android app is
a shell; the engine and the viewer are independent.

## Status

Early. The vertical slice `feed -> detect -> score -> thesis -> store` and the
backtest harness are built. Validation, delivery, rendering, CLI, web UI and the
scan orchestrator are contract-defined but not implemented.

See [docs/BACKLOG.md](docs/BACKLOG.md) for the authoritative list of work.

## Layout

```
canslim/
  contracts.py          Protocols + normalized dataclasses. The engine boundary.
  registry.py           Generic name -> factory registry for every plugin kind.
  universe.py           NSE/S&P loaders, tiered scan plan, rate-limited fetch.
  backtest.py           Walk-forward harness, look-ahead truncated.
  datafeed/             DataFeed implementations.
  patterns/             PatternDetector implementations.
  strategy/             Strategy implementations.
  store/                Persistence (SQLite).
  validate/             Validator implementations.        [empty]
  delivery/             DeliveryAdapter implementations.  [empty]
  render/               Chart/report rendering.           [empty]
fixtures/               Test fixtures.                    [empty]
tests/                  Tests.                            [empty]
```

## Docs

| Doc | Purpose |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How the pieces fit together, as-built |
| [docs/BACKLOG.md](docs/BACKLOG.md) | Every change we intend to make, with IDs |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Decisions made and open questions |
| [docs/DATA-SOURCES.md](docs/DATA-SOURCES.md) | What each data source actually returns (measured) |

## Environment

Python 3.14 virtualenv at `.venv`.

```bash
cd ~/canslim
.venv/bin/python -c "import canslim; print('ok')"
```

There is no CLI and no entrypoint yet. See `CLI-*` items in the backlog.
