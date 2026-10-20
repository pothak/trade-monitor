# Spec

This folder is the **forward-looking design** of trade-monitor: what we intend
to build and why.

## How this relates to `docs/`

Two folders, two jobs. They must not both claim to be the truth.

| Folder | Time direction | Authority |
|---|---|---|
| `spec/` | **Forward** — the target state | When spec and code disagree, the code is *not yet* done |
| `docs/` | **Backward** — as-built and history | When spec and code disagree, `docs/ARCHITECTURE.md` says what is real today |

The loop is:

```
  spec/  ──describes──►  what we intend
    ▲                          │
    │ update the spec          │ build it
    │ to match reality         ▼
  docs/ARCHITECTURE.md  ◄──records──  what exists
```

If you build something that departs from the spec, you either fix the code or
update the spec. The spec is never allowed to silently drift into fiction:
every spec doc carries a **Status** line saying how much of it exists.

## Status vocabulary

| Status | Meaning |
|---|---|
| `PROPOSED` | agreed direction, nothing built |
| `PARTIAL` | some of it exists, some does not |
| `BUILT` | implemented and verified |
| `SUPERSEDED` | replaced; kept for history |

## The rule that makes a spec useful

**A spec document must be falsifiable.** "The dashboard should be nice" is not
spec. "The dashboard serves GET /api/theses returning an array of thesis
objects; the UI polls it every 10 s" is spec, because you can check it.

So every document here aims to state things that can be verified against the
code, and each says plainly which parts are still fiction.

## Reading order

| Doc | Question it answers |
|---|---|
| [PRODUCT.md](PRODUCT.md) | What is this, for whom, and what is it *not*? |
| [PRINCIPLES.md](PRINCIPLES.md) | What invariants must never break? |
| [COMPONENTS.md](COMPONENTS.md) | What are the parts and their responsibilities? |
| [PLUGINS.md](PLUGINS.md) | How do users write their own strategies? |
| [STREAMING.md](STREAMING.md) | How does the live path work, and why two cadences? |
| [DATA-MODEL.md](DATA-MODEL.md) | How is data stored and kept point-in-time safe? |
| [UI.md](UI.md) | What is the dashboard? |
| [INSTALL.md](INSTALL.md) | How does someone get it running? |
| [ROADMAP.md](ROADMAP.md) | In what order, and what counts as done? |
