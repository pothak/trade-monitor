# Philosophy: framework, not opinion

**Status** PROPOSED — this document states the *why*. It must drive the
roadmap, the plugin API, and the audit design. Where it conflicts with an
earlier decision, this wins and the earlier decision is revisited.

---

## The core idea

> **The value is not the strategies. The value is the framework.**

A DIY investor already has an idea. What they do not have is the boring,
error-prone plumbing that makes an idea trustworthy:

- clean, point-in-time market data
- a runtime that runs a strategy the same way every time
- a record of what was seen, when, and why
- freedom from a subscription and from a black box

trade-monitor supplies the plumbing. The user supplies the thesis.

This has a blunt consequence: **CAN SLIM and the cup-with-handle detector are
demos, not the product.** They exist to prove the framework works and to teach
the API by example. If the app is only useful with our strategies, we have built
the wrong thing.

---

## Three requirements that follow

### R1 — No subscription, no gate, no bait

The whole app must run on a user's own machine with no paid service in the
critical path. Not a free tier, not a cloud account, not an API key.

*What this rules out.* Any feature that cannot work without a paid feed stays
out, or is clearly optional and never required for the core loop.

*What this allows.* Optional integrations (a broker, a paid data provider)
behind an interface, so a user who wants them can add them, and a user who does
not is never blocked.

### R2 — The record is verifiable, not merely stored

"Append-only" stops accidents. It does not stop a determined rewrite, and it
does not let a user *prove* anything to themselves or anyone else.

Trustworthy means:

- **hash-chained** — each event embeds the hash of its predecessor, so any gap,
  reorder or edit is detectable
- **content-addressed** — each event hashes the data it cites, so a claim like
  "the close was 1202" is checkable against the stored bar
- **replayable** — re-running from the ledger reproduces the same decisions,
  which is what makes a backtest credible

This turns "we do not manipulate the record" from an assertion into a check
anyone can run. See [DATA-MODEL.md](DATA-MODEL.md) and backlog `INFRA` work.

### R3 — The framework has no market opinion

The app never decides what is a good trade. It provides contracts and a
lifecycle; the user provides the judgment.

*What this means concretely.* A strategy is free to ignore fundamentals, ignore
patterns, and rank by anything. Nothing in the engine assumes a strategy looks
like CAN SLIM. The proof is a shipped reference strategy that is
philosophically *opposite* to CAN SLIM (see [PLUGINS.md](PLUGINS.md)).

---

## The honesty problem with free data

No subscription means free data, and free data is imperfect. Measured:

| Source | Cost | Reality |
|---|---|---|
| yfinance | free | India quotes work; `institutional_holders` empty for every `.NS` |
| NSE MCP | free | **2023 entirely missing**; `deliveryQty` always 0 |

A wrong or missing bar is a form of distortion — not deliberate, but real. The
framework cannot fix it, so it must **disclose it**.

The rule:

> **Never present a confident number built on thin data. Say what is missing.**

This is Principle P8, enforced through `Fundamentals.degraded`, coverage-scaled
confidence, and a UI that shows data quality next to every recommendation.

Transparency is the substitute for a paid feed. We do not hide the gap; we
label it.

---

## What "help the DIY investor" concretely means

A user should be able to go from idea to running monitor without touching the
engine:

1. **Write one file.** A strategy is a name, a `score`, and a `thesis`.
2. **Drop it in a folder.** It is discovered at runtime. No rebuild.
3. **Trust the output.** Every decision carries the evidence that produced it.
4. **Check the record.** The ledger can be verified and replayed.
5. **Own it.** No key, no account, no phone-home, no subscription.

If any of those five needs an exception, the framework has failed at its job.
