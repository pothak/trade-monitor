# Plugins: how users write strategies

**Status** BUILT — the seam works end to end; see the three shipped reference
strategies.

This is the heart of the product. A DIY investor brings an idea as **one Python
file**. It is discovered and loaded at runtime, with no rebuild and no engine
import.

---

## The five-step promise

1. **Write one file.**
2. **Drop it in a folder.** It is discovered automatically.
3. **It appears in the list.** `trade-monitor strategies` shows it.
4. **It runs.** `scan` or the dashboard uses it like any built-in.
5. **Its decisions are auditable** in the ledger, like any built-in.

If any step needs an exception, the plugin system is broken.

---

## What a strategy depends on

**Only the contracts package.** Never the engine.

```python
from trade_monitor_contracts import (
    Bar, PriceSeries, Fundamentals, Evidence, Thesis, Invalidation,
    DataFeed, PatternDetector, Strategy, Validator, DeliveryAdapter,
    Grade, Market,
)
```

`trade-monitor-contracts` has **zero dependencies** (Principle D4). A plugin
never inherits the engine's dependency tree, so the engine can change
underneath without breaking plugins.

---

## The minimum a strategy needs

```python
from trade_monitor_contracts import Evidence, Invalidation, Thesis
import hashlib

class MyIdea:
    name = "my_idea"
    version = "0.1.0"
    requires = ()              # pattern detectors needed (none here)
    needs_fundamentals = False # price-only: skip expensive fetches

    def score(self, series, fundamentals, patterns=None, **options):
        # return (evidence, grade, confidence)
        ev = [
            Evidence("roc", "20d return", 12.5, 10.0, "pass",
                     "12.5% over 20 bars (want >= 10%)"),
        ]
        return ev, "B", 72

    def thesis(self, *, series, fundamentals, patterns=None,
               evidence, grade, confidence, **options):
        as_of = series.last.date
        tid = hashlib.sha256(f"{series.symbol}|{as_of}".encode()).hexdigest()[:16]
        return Thesis(
            id=tid, symbol=series.symbol, name=series.symbol,
            market=series.market, currency=series.currency, as_of=as_of,
            strategy=self.name, grade=grade, confidence=confidence,
            evidence=list(evidence),
            invalidation=Invalidation(rule="close below entry", time_stop_days=20),
        )

STRATEGY = MyIdea()   # discovered by the loader
```

Drop it in `strategies/` and it works.

---

## The contract, precisely

| Attribute | Meaning | Default |
|---|---|---|
| `name` | registry key, unique | required |
| `version` | for the ledger record | `0.0.0` |
| `requires` | pattern detector names needed | `()` |
| `needs_fundamentals` | whether to fetch fundamentals | `False` |

| Method | Returns | Called |
|---|---|---|
| `score(series, fundamentals, patterns=None, **options)` | `(list[Evidence], Grade, int)` | for every candidate |
| `thesis(*, series, fundamentals, patterns, evidence, grade, confidence, **options)` | `Thesis` | for scored candidates |

Return shapes the engine also accepts: a bare grade string, or `(grade, score)`.

---

## `needs_fundamentals` is a performance switch

Setting it `False` is not cosmetic. The engine's whole performance model is
*never spend fundamentals cost on a name that cannot pay off* (Principle P3).

```
needs_fundamentals = False  ->  fundamentals are NEVER fetched for this strategy
```

The three shipped reference strategies are all `False`, so they cost zero
fundamental fetches and run in milliseconds per symbol.

---

## Three reference strategies ship free

They exist to teach the API and to prove the framework has no market opinion.

| File | Philosophy | Patterns | Fundamentals |
|---|---|---|---|
| `breakout_proximity.py` | base about to break | cup-with-handle | no |
| `momentum_rank.py` | strength already moving | none | no |
| `mean_reversion.py` | weakness about to snap back | none | no |
| `trade_monitor/strategy/can_slim.py` | CAN SLIM (built-in) | cup-with-handle | yes |

**They disagree, on purpose.** On the same day, `momentum_rank` grades ITC.NS
`F` while `mean_reversion` grades it `B`. A momentum strategy dislikes exactly
what a mean-reversion strategy likes. The engine takes no side — which is the
whole point.

A user can read all four and see four different ways to express an idea.

---

## Loading rules

The loader (`trade_monitor/strategy/loader.py`) scans, in order:

```
strategies/                          (project-local; ships with the repo)
~/.trade-monitor/strategies/         (per-user)
~/.config/trade-monitor/strategies/  (per-user, XDG)
```

A file is loaded if it defines one of:

- `STRATEGY` — one instance
- `STRATEGIES` — an iterable of instances
- `register(registry)` — a function that registers directly

**Fault isolation (Principle P6).** A file that raises on import, defines
nothing, or defines something malformed is **skipped and logged**. The rest
still load. A broken plugin never stops the monitor.

**Shadowing.** A user strategy may reuse a built-in name. The later one wins and
the shadowing is logged (`strategy.shadowed`) so it is never a silent surprise.

---

## Writing a data feed or a pattern (advanced)

The same seam applies to the other plugin kinds. A `DataFeed` implements
`price_series`, `fundamentals`, `index_for`. A `PatternDetector` declares
`provides` (e.g. `pivot_price`, `stop_reference`) and returns a dict of measured
values. Both are registered the same way.

---

## The plugin console

The engine's value is "bring your own component". The console is how you see
and manage everything you have brought, across **all five plugin kinds**:

| Kind | Protocol | Directory | Declarative form |
|---|---|---|---|
| strategy | `Strategy` | `strategies/` | `.py` (needs code) |
| feed | `DataFeed` | `feeds/` | `.toml` (no code) |
| pattern | `PatternDetector` | `patterns/` | `.py` |
| validator | `Validator` | `validators/` | `.py` |
| deliverer | `DeliveryAdapter` | `deliverers/` | `.py` |

### Two ways to define a plugin

**1. Code (any kind).** Drop a `.py` in the kind's directory, as today. Full
power, authored in your own editor. The console lists and toggles it but does
not edit source — authoring stays in the editor (low-friction philosophy).

**2. Declarative spec (feeds, and later patterns).** A `.toml` file with no
code, interpreted by a generic driver. This is how "create a new feed source"
works without uploading executable code:

```toml
# ~/.trade-monitor/feeds/mydata.toml
[feed]
name        = "mydata"
kind        = "rest"          # the generic driver to use
description = "My broker's REST quote endpoint"
enabled     = true

[feed.http]
url         = "https://api.example.com/quote/{symbol}"
method      = "GET"
# api_key read from the environment, never stored in the file
auth_header = "Authorization"
auth_env    = "MYDATA_TOKEN"
timeout     = 10

[feed.map]                    # json path -> Bar / PriceSeries field
symbol      = "$.ticker"
bars        = "$.candles"
date        = "t"             # relative to each item in `bars`
open        = "o"
high        = "h"
low         = "l"
close       = "c"
volume      = "v"

[feed.markets]
IN = "^NSEI"
US = "^GSPC"
```

The generic driver (`GENERIC_FEED` registry kind, `"rest"`) executes this:
fetch, map, build `PriceSeries`. No `exec`, no arbitrary code. Secrets come
from the environment, named by `auth_env`, so credentials never live in a file
the console can read back.

### The manifest console

The console never executes a plugin to describe it. It reads:

- **code plugins** — a static scan for `name` / `requires` / `needs_fundamentals`,
  plus an import under fault isolation to confirm it loads
- **spec plugins** — the TOML file directly

and produces a uniform **manifest**:

```json
{
  "name": "mydata", "kind": "feed", "origin": "spec",
  "version": "0.1.0", "enabled": true, "status": "ok",
  "description": "My broker's REST quote endpoint",
  "params": {"url": "https://api.example.com/quote/{symbol}"},
  "config_schema": {}
}
```

`status` is one of `ok`, `error`, `disabled`. A plugin that fails to load shows
`error` with the reason — it never disappears silently.

### API

```
GET    /api/plugins                     all kinds, uniform manifests
GET    /api/plugins?kind=feed           filtered
GET    /api/plugins/<kind>/<name>       one manifest + source location
POST   /api/plugins                     create (spec kinds) or register (code)
PUT    /api/plugins/<kind>/<name>       update params / description
DELETE /api/plugins/<kind>/<name>       remove the file (code + spec)
POST   /api/plugins/<kind>/<name>/toggle   enable / disable
POST   /api/plugins/validate            syntax-check without loading
```

Creating a **code** plugin writes a minimal, valid starter file into the kind's
directory — a template the user then edits in their editor. Creating a **spec**
plugin writes the `.toml` from the submitted values. Neither path runs user
code from the console.

### Enable / disable

A disabled plugin is **skipped at load time** and recorded as `status=disabled`.
The engine and CLI select from enabled plugins only. Disabling is reversible and
never deletes the file.

### Discovery order (all kinds)

```
<kind-dir>/                          project-local
~/.trade-monitor/<kind-dir>/         per-user
~/.config/trade-monitor/<kind-dir>/  XDG
```

Later directories shadow earlier ones by name, as with strategies. Shadowing is
logged so it is never a silent surprise.

### Fault isolation still holds (P6)

A broken plugin — bad TOML, a syntax error, a class that fails to instantiate —
is recorded as `status=error` with its reason and skipped. The console shows it;
the monitor keeps running.
