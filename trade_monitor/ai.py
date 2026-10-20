"""AI interpretation layer (optional).

The engine's grades, numbers and decisions are deterministic and must stay
that way. This module adds one thing only: a plain-language *explanation* of
what the engine already concluded. The model is handed a frozen summary and
asked to translate it -- it is never asked to score, rank, or invent a figure,
and its output is never written back into a thesis.

Design rules (see spec/AI.md):
  1. Explain-only. The model may not produce numbers that are not already in
     the input; the prompt forbids it and the input is self-contained.
  2. Off by default. No key, no call. Enabling is explicit.
  3. Local-first. Default backend is a local Ollama server so the whole thing
     stays self-hosted and free; a cloud backend is opt-in via env vars.
  4. Never fatal. Any failure returns {ok: False, reason} so the UI shows why
     instead of breaking the page.
  5. Cached. Interpretations are stored by thesis id so the same card is not
     re-billed on every page load.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

DEFAULT_MODEL = os.environ.get("TM_AI_MODEL", "qwen2.5-coder:7b-instruct")
OLLAMA_URL = os.environ.get("TM_OLLAMA_URL", "http://127.0.0.1:11434")

SYSTEM_PROMPT = (
    "You are a calm, plain-spoken analyst writing for a DIY investor. "
    "You are given a stock's already-computed grade and evidence from a rules "
    "engine. Explain, in 2-3 short sentences, what this verdict means and what "
    "the investor should watch. RULES: use ONLY the numbers provided; never "
    "invent or estimate a price, percentage or date; never give buy/sell "
    "advice; never change the grade. If data is marked degraded, say so plainly."
)


def enabled() -> bool:
    """AI is opt-in. Enabled only when TM_AI=1 is set."""
    return os.environ.get("TM_AI", "") in ("1", "true", "yes")


def _backend() -> str:
    return os.environ.get("TM_AI_BACKEND", "ollama").lower()


def _summary(thesis: dict, technicals: dict | None) -> str:
    """A self-contained fact sheet -- the only thing the model may use."""
    t = thesis or {}
    inv = t.get("invalidation") or {}
    facts = {
        "symbol": t.get("symbol"),
        "strategy": t.get("strategy"),
        "grade": t.get("grade"),
        "confidence": t.get("confidence"),
        "as_of": t.get("as_of"),
        "current_price": (technicals or {}).get("last"),
        "expected_ceiling": t.get("measured_move_target"),
        "stop_price": inv.get("stop_price"),
        "time_stop_days": inv.get("time_stop_days"),
        "trend": (technicals or {}).get("trend"),
        "rsi14": (technicals or {}).get("rsi14"),
        "degraded_inputs": t.get("degraded_inputs") or [],
    }
    return json.dumps(facts, default=str)


def _call_ollama(model: str, prompt: str, timeout: float) -> str:
    body = json.dumps({
        "model": model,
        "stream": False,
        "prompt": f"{SYSTEM_PROMPT}\n\nFACT SHEET:\n{prompt}",
    }).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate", data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read()).get("response", "").strip()


def _call_openai_compatible(prompt: str, timeout: float) -> str:
    base = os.environ.get("TM_AI_URL", "https://api.openai.com/v1")
    key = os.environ.get("TM_AI_KEY", "")
    model = os.environ.get("TM_AI_MODEL", "gpt-4o-mini")
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"FACT SHEET:\n{prompt}"},
        ],
        "temperature": 0.2,
    }).encode()
    req = urllib.request.Request(
        f"{base}/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read())
        return d["choices"][0]["message"]["content"].strip()


def interpret(thesis: dict, technicals: dict | None = None,
              *, timeout: float = 30.0) -> dict:
    """Return a plain-language reading of one thesis.

    Always returns a dict. On any failure: {ok: False, reason}. Never raises,
    never mutates the thesis.
    """
    if not enabled():
        return {"ok": False, "reason": "AI is off (set TM_AI=1 to enable)"}

    prompt = _summary(thesis, technicals)
    backend = _backend()
    try:
        if backend == "ollama":
            text = _call_ollama(os.environ.get("TM_AI_MODEL", DEFAULT_MODEL),
                                prompt, timeout)
        else:
            if not os.environ.get("TM_AI_KEY"):
                return {"ok": False, "reason": "TM_AI_KEY not set for cloud backend"}
            text = _call_openai_compatible(prompt, timeout)
    except urllib.error.URLError as exc:
        return {"ok": False, "backend": backend,
                "reason": f"cannot reach model backend: {exc.reason}"}
    except Exception as exc:  # noqa: BLE001 - optional feature must not crash
        return {"ok": False, "backend": backend,
                "reason": f"{type(exc).__name__}: {exc}"}

    if not text:
        return {"ok": False, "backend": backend, "reason": "empty response"}
    return {"ok": True, "backend": backend, "text": text,
            "model": os.environ.get("TM_AI_MODEL", DEFAULT_MODEL)}


__all__ = ["interpret", "enabled", "SYSTEM_PROMPT"]
