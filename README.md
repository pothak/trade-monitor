# trade-monitor

**A self-hosted framework for running your own trading strategies.**

Bring your own idea as one Python file. trade-monitor supplies the plumbing you
should not have to rebuild: clean market data, point-in-time correctness, a
runtime, and a verifiable audit trail. No account, no API key, no subscription,
MIT licensed.

![status](https://img.shields.io/badge/status-early-orange)
![license](https://img.shields.io/badge/license-MIT-green)

## Quick start

```bash
git clone https://github.com/pothak/trade-monitor
cd trade-monitor
./install.sh            # or: SEED=25 ./install.sh  to also fetch demo data
trade-monitor serve     # dashboard at http://127.0.0.1:8787
```

## What it does

```
feeder ──► watchlist ──► strategy ──► confirm ──► recommend ──► audit
(data)      (items)       (score)     (1-2 days)   (signal)     (ledger)
```

It pulls market data, runs *your* strategies over a watchlist, re-confirms
setups over a day or two, issues recommendations, and records every step in an
append-only, replayable ledger.

## Bring your own strategy

A strategy is **one file**. Drop it in `strategies/` and it loads at runtime —
no rebuild, no engine import.

```python
from trade_monitor_contracts import Evidence, Invalidation, Thesis

class MyIdea:
    name = "my_idea"
    requires = ()               # pattern detectors needed
    needs_fundamentals = False  # price-only: skip expensive fetches

    def score(self, series, fundamentals, patterns=None, **options):
        ev = [Evidence("roc", "20d return", 12.5, 10.0, "pass", "12.5% over 20 bars")]
        return ev, "B", 72

    def thesis(self, *, series, fundamentals, patterns=None,
               evidence, grade, confidence, **options):
        return Thesis(id="...", symbol=series.symbol, name=series.symbol,
                      market=series.market, currency=series.currency,
                      as_of=series.last.date, strategy=self.name,
                      grade=grade, confidence=confidence, evidence=list(evidence),
                      invalidation=Invalidation(rule="close below entry", time_stop_days=20))

STRATEGY = MyIdea()
```

Three free reference strategies ship to show the API — and to prove the
framework has **no market opinion**:

| Strategy | Philosophy |
|---|---|
| [`canslim`](trade_monitor/strategy/can_slim.py) | strength off a base (built-in) |
| [`momentum_rank`](strategies/momentum_rank.py) | strength already moving |
| [`mean_reversion`](strategies/mean_reversion.py) | weakness about to bounce |
| [`breakout_proximity`](strategies/breakout_proximity.py) | base near a breakout |

They **disagree on purpose**: on the same day `momentum_rank` grades ITC.NS `F`
while `mean_reversion` grades it `B`. The engine takes no side.

## Principles

The invariants that must never break, and how each is enforced — see
[spec/PRINCIPLES.md](spec/PRINCIPLES.md):

- **No look-ahead, ever** — point-in-time reads enforced by construction
- **One code path** for live and backtest, so backtests predict live behaviour
- **Gated expensive work** — fundamentals only for names that can pay off
- **Every decision auditable** — an append-only event ledger
- **Framework, not opinion** — the app never says what a good trade is

## Layout

```
trade_monitor_contracts/   the plugin interface (separate, zero dependencies)
trade_monitor/             the engine (core is stdlib-only)
  engine.py                batch scan orchestration
  stream.py, live.py       streaming compute + bar-close decisions
  store/                   engine state, point-in-time market data, ledger
  strategy/                CAN SLIM + the runtime plugin loader
  server/                  JSON API + dashboard
  cli.py                   command line
strategies/                ★ your strategies, auto-loaded
spec/                      forward-looking design (what we intend)
docs/                      as-built and history (what exists)
```

## Commands

```bash
trade-monitor serve                      # dashboard
trade-monitor scan --strategy canslim --market IN
trade-monitor strategies                 # list loaded strategies
trade-monitor seed --limit 50            # pull data
trade-monitor ledger --limit 30          # the audit trail
```

## Docs

| Doc | Purpose |
|---|---|
| [spec/](spec/README.md) | **forward-looking design** — the direction |
| [spec/PHILOSOPHY.md](spec/PHILOSOPHY.md) | why a framework, not a product |
| [spec/PLUGINS.md](spec/PLUGINS.md) | how to write a strategy |
| [spec/DEPENDENCIES.md](spec/DEPENDENCIES.md) | open-source-only policy |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | as-built architecture |
| [docs/BACKLOG.md](docs/BACKLOG.md) | every intended change, with IDs |
| [docs/DATA-SOURCES.md](docs/DATA-SOURCES.md) | what each source actually returns |
| [NOTICE.md](NOTICE.md) | third-party licenses |

## License

MIT — see [LICENSE](LICENSE). Free to use, modify, and build on, for anyone,
personal or commercial. No subscription in the critical path.
