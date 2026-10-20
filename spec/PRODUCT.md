# Product

**Status** PARTIAL — core engine built; delivery, UI and packaging not built.

## One sentence

A self-hosted trade monitor that pulls market data, runs *your* strategies over
a watchlist, confirms setups over a day or two, and issues recommendations with
a complete, replayable audit trail.

## The problem it solves

A DIY trader runs a strategy in their head or in a spreadsheet. Three things go
wrong:

1. **Coverage.** You cannot watch 2,500 stocks by hand, so you miss setups.
2. **Discipline.** You fudge the rules when a name is exciting.
3. **Memory.** You cannot remember what you saw, when, or why you acted.

trade-monitor automates coverage, enforces the rules mechanically, and remembers
everything.

## The pipeline

```
  feeder ──► watchlist ──► strategy ──► confirm ──► recommend ──► audit
  (data)      (items)       (score)     (1-2 days)   (signal)     (ledger)
```

- **feeder** pulls data from a provider on a schedule
- **watchlist** is *generated*: names that show a pattern become items
- **strategy** scores each item and builds a thesis
- **confirm** re-checks over 1–2 days that the setup still holds
- **recommend** issues an actionable signal, or withholds it
- **audit** records every step, forever, replayably

## Who it is for

A single DIY trader running it on their own machine, with their own strategies.
Not a fund, not a team, not a service. Everything assumes **one user, one
machine, local data**.

## What it is *not*

Non-goals, so scope does not creep:

- **Not a broker.** It never places orders. It recommends; the human acts.
- **Not investment advice.** Output is informational.
- **Not a backtest-only tool.** Backtesting exists to validate strategies, not
  as the product.
- **Not multi-user.** No accounts, no permissions, no tenancy.
- **Not a data vendor.** It consumes free/open data sources and degrades when
  they are incomplete.

## Two ways to produce decisions

The same strategies run in two modes, and they must give the same answers:

| Mode | Trigger | Use |
|---|---|---|
| **Batch scan** | scheduled / on demand | the daily sweep over the universe |
| **Live stream** | bar close on a stream of ticks | react the moment a bar completes |

The invariant: live and backtest run one code path, so a backtest number
predicts live behaviour. See [STREAMING.md](STREAMING.md).

## CAN SLIM is an example, not the point

The app ships CAN SLIM as a *reference strategy* — a worked example of the
plugin API. The app is a **strategy host**. A user's own strategy is a first
class citizen equal to the built-in one. See [PLUGINS.md](PLUGINS.md).

## Success looks like

- A new strategy is one file, dropped in a folder, picked up with no rebuild.
- A scan over 2,500 names spends expensive work only on names that can pay off.
- Any recommendation can be traced, event by event, back to the data that made it.
- A stranger can `./install.sh`, open a browser, and see their strategies running.
