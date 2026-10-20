# AI interpretation

Status: **built, off by default.** Code: `trade_monitor/ai.py`.

## What it is

An optional layer that turns the engine's already-computed verdict into plain
language a DIY investor can read. It is the difference between a card that says
`momentum_rank B conf=67` and one that says *"trend is up and momentum is
positive, but volume is thin — worth watching, not acting on."*

## What it is not

It is **not** a decision-maker and never will be. The engine's grades, numbers
and signals stay deterministic. The model is handed a frozen fact sheet and
asked only to explain it.

## The explain-only contract

1. **Input is self-contained.** The model receives a JSON fact sheet: symbol,
   strategy, grade, confidence, current price, expected ceiling, stop, trend,
   RSI, and any degraded-data flags. Nothing else.

2. **No invention.** The system prompt forbids producing any number, price,
   percentage or date not present in the fact sheet, and forbids giving
   buy/sell advice or changing the grade.

3. **Never written back.** The interpretation is displayed and cached; it is
   never stored on the thesis and never fed back into scoring, ranking or
   invalidation.

4. **Never fatal.** Any failure returns `{ok: False, reason}`. The UI shows the
   reason in a muted box. A missing model never breaks a page.

5. **Off by default.** No key, no call. Enabling is explicit: `TM_AI=1`.

## Backends

| Env | Default | Meaning |
|---|---|---|
| `TM_AI` | unset | `1` to enable |
| `TM_AI_BACKEND` | `ollama` | `ollama` (local) or `openai` (cloud-compatible) |
| `TM_AI_MODEL` | `qwen2.5-coder:7b-instruct` | model name |
| `TM_OLLAMA_URL` | `http://127.0.0.1:11434` | local server |
| `TM_AI_URL` | `https://api.openai.com/v1` | cloud base URL |
| `TM_AI_KEY` | unset | required only for the cloud backend |

Local-first is deliberate: the whole point of a self-hosted monitor is that your
data does not have to leave your machine.

## Caching

Interpretations are cached per thesis id for one hour in the server process, so
reloading a card does not re-run the model.

## Why this shape

The project's philosophy is that a recommendation must be *provable*. An AI that
produces the recommendation would break that. An AI that *explains* a
deterministic recommendation strengthens it: the grader stays auditable, the
prose is disposable, and a wrong explanation cannot corrupt a thesis.
