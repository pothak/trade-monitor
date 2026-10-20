# Dependencies & licensing

**Status** BUILT — policy applies today; enforced at install time.

The rule: **open source only, permissive licenses only, and the core stays
stdlib-only.** Anyone must be able to run this, read this, fork this, and
redistribute it, with no legal or toolchain gate.

---

## D1 — Open source only

Every dependency must be OSI-approved open source. No proprietary SDKs, no
trial-ware, no "free tier" cloud services required at runtime.

## D2 — Permissive licenses only

Allowed: **MIT, Apache-2.0, BSD (2/3-Clause), ISC, PSF, Zlib, 0BSD, CC0.**

Disallowed: **GPL, AGPL, LGPL, SSPL, BUSL** — not because they're bad, but
because they restrict redistribution and this project must stay freely
redistributable and embeddable.

## D3 — The core is stdlib-only

`trade_monitor/` (engine, stream, live, store, registry, contracts) imports
**only the Python standard library** at runtime. Third-party libraries are
confined to the edges — the datafeed implementations.

*Why.* It keeps the core auditable, dependency-free, and portable. It also means
the contract package a strategy author installs has no dependencies at all.

*Verify.*

```
grep -rE "^(import|from) (pandas|numpy|yfinance|requests|bs4)" trade_monitor/ \
  --include=*.py | grep -v datafeed/
# must print nothing
```

## D4 — The interface has zero dependencies

`trade_monitor_contracts` imports only the standard library, on purpose. A
strategy author installs the contract, not the engine, and never inherits a
dependency tree.

## D5 — Declared and verified at install

`install.sh` verifies licenses of the resolved dependency set and fails loudly
if a disallowed license appears. Policy is enforced by tooling, not memory.

---

## Current inventory (verified 2026-10-20)

All permissive. All OSI-approved. **No copyleft anywhere.**

| Package | License | Where used |
|---|---|---|
| yfinance | Apache-2.0 | `datafeed/yfinance_feed.py` |
| pandas | BSD-3-Clause | `datafeed/` (transitively via yfinance) |
| numpy | BSD-3 + 0BSD + MIT + Zlib + CC0 | transitively |
| requests | Apache-2.0 | `universe.py` (universe list fetch) |
| beautifulsoup4 | MIT | reserved for NSE scraping (DATA-5) |
| exchange_calendars | Apache-2.0 | trading-calendar correctness |
| matplotlib | PSF | chart rendering (planned) |
| pytest | MIT | tests only, never runtime |

**Core runtime third-party dependencies: none.**

---

## This project's own license

**trade-monitor is MIT.** Chosen deliberately, under
[PHILOSOPHY.md](PHILOSOPHY.md) R1 (no gate) and the goal of helping any DIY
investor.

What MIT means for this project and its users:

| Anyone may… | May they? |
|---|---|
| Use it personally | ✅ |
| Use it commercially, to make profit | ✅ |
| Modify it | ✅ |
| Redistribute it, or sell a product built on it | ✅ |
| Be required to pay a royalty | ❌ no |
| Be required to open-source their own code | ❌ no |
| Redistribute it without the copyright notice | ❌ must keep the notice |

Consequences, stated honestly:

- **The maintainers can also build a commercial product** (hosting, paid
  strategies, support) on top of the MIT framework.
- **No exclusivity.** Anyone, including a competitor, may take trade-monitor and
  build a paid product without contributing back. This is the accepted cost of
  maximum adoption and of "anyone may use it".
- **Not legal advice.** License questions with money attached should be checked
  with a lawyer; this is a plain-language summary of standard MIT terms.

## Dashboard stack

The dashboard must obey D1–D5 like everything else. The binding constraint is
not merely "open source" — it is **"usable by anyone"**, which rules out
requiring a JavaScript toolchain to run the app.

| Option | License | Usable by anyone? | Verdict |
|---|---|---|---|
| Stdlib `http.server` + vanilla JS + a permissively-licensed chart lib | PSF / MIT | ✅ Python only | **Preferred** |
| Flask + server-rendered HTML | BSD-3 | ✅ Python only | acceptable |
| React/Next + Node build step | MIT | ❌ needs Node + build | **rejected under this policy** |

A Node build step is open source, but it is a **toolchain gate**: a user without
Node cannot run the app from source. That conflicts with the "anyone can use it"
requirement, so the dashboard is specified as **Python-served with a
no-build frontend**. See [UI.md](UI.md).
