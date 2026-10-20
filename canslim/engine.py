"""Scan orchestration.

Runs one pass: plan -> prices -> patterns -> fundamentals -> score -> thesis ->
store. Any number of patterns and any number of strategies can run in the same
pass, and every strategy sees identical inputs, so results are comparable.

Latency design (the whole point of this module)
------------------------------------------------
A full-universe scan is ~2500 symbols. The spectrum of work is very lopsided:

    price fetch      ~1 network call        cheap-ish, unavoidable
    pattern detect   pure CPU, no network   microseconds
    fundamentals     several network calls  EXPENSIVE (~10x a price fetch)

So the pass is ordered cheapest-filter-first and the expensive step is gated:

    1. fetch prices                (tier 2 stops here)
    2. run every detector          (pure CPU)
    3. any pattern fired?          -> if none, record a miss and STOP
    4. fetch fundamentals          ONLY for survivors
    5. score + build thesis

Steps 1-2-3 are cheap and cover the whole universe. Only step 4 costs, and only
names that already have a chart setup pay it. `strategy.needs_fundamentals` lets
a price-only strategy skip step 4 entirely.

Guarantees:
  - every scanned symbol is recorded, hits and misses, so we keep a population
  - a failure on one symbol never aborts the pass
  - detectors and strategies are selected by name from the registries
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from .contracts import (
    Fundamentals,
    PriceSeries,
    Thesis,
    ValidationResult,
)
from .patterns import REGISTRY as PATTERNS
from .strategy import REGISTRY as STRATEGIES
from .universe import RateLimiter, scan_fundamentals, scan_prices

# Default lookback: enough for a 65-bar cup plus a 250-bar high window.
DEFAULT_LOOKBACK = 300


@dataclass
class ScanReport:
    """What one strategy produced over one universe."""

    strategy: str
    market: str
    scanned: int = 0
    errors: int = 0
    misses: int = 0
    theses: list[Thesis] = field(default_factory=list)
    elapsed_s: float = 0.0
    observations: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def hits(self) -> int:
        return len(self.theses)

    def top(self, n: int = 10) -> list[Thesis]:
        """Highest confidence first. This is what you would actually look at."""
        return sorted(self.theses, key=lambda t: t.confidence, reverse=True)[:n]

    def summary(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "market": self.market,
            "scanned": self.scanned,
            "errors": self.errors,
            "misses": self.misses,
            "patterns_fired": self.scanned - self.misses,
            "hits": self.hits,
            "hit_rate": round(self.hits / self.scanned, 4) if self.scanned else None,
            "elapsed_s": round(self.elapsed_s, 2),
            "per_symbol_ms": round(self.elapsed_s / self.scanned * 1000, 1) if self.scanned else None,
            "notes": self.notes,
        }


def detect_patterns(
    series: PriceSeries,
    pattern_names: Sequence[str],
    *,
    pattern_options: Mapping[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Pure CPU. Run every requested detector; return the ones that fired.

    Detectors are independent: one raising does not stop the others.
    """
    opts = pattern_options or {}
    fired: dict[str, dict[str, Any]] = {}
    for name in pattern_names:
        detector = PATTERNS.create(name)
        try:
            geom = detector.detect(series, **opts.get(name, {}))
        except Exception:  # noqa: BLE001 - a broken detector must not stop a scan
            geom = None
        if geom:
            fired[name] = geom
    return fired


def validate_thesis(
    thesis: Thesis,
    validators: Sequence[Any],
) -> tuple[Thesis, list[ValidationResult]]:
    """Apply validators, enforcing the model-cannot-override-rule invariant.

    Rule validators that are `blocking` and fail will reject the thesis. Model
    validators may only adjust confidence within the remaining headroom, and are
    ignored entirely when a rule validator has already failed.
    """
    results: list[ValidationResult] = []
    rule_failed = False
    delta = 0

    for v in validators:
        try:
            res = v.validate(thesis)
        except Exception:  # noqa: BLE001 - a broken validator is not fatal
            continue
        results.append(res)
        kind = getattr(res, "kind", getattr(v, "kind", "rule"))
        if kind == "rule":
            if not res.passed and res.blocking:
                rule_failed = True
        elif kind == "model":
            # Model influence is void once a blocking rule has failed.
            if not rule_failed:
                delta += int(res.confidence_delta or 0)

    if rule_failed:
        new_conf = 0
    else:
        new_conf = max(0, min(100, thesis.confidence + delta))

    if new_conf != thesis.confidence:
        import dataclasses

        thesis = dataclasses.replace(thesis, confidence=new_conf)
    return thesis, results


def run_strategy(
    strategy_name: str,
    symbols: Sequence[str],
    *,
    feed: Any,
    market: str = "IN",
    lookback: int = DEFAULT_LOOKBACK,
    workers: int = 8,
    limiter: RateLimiter | None = None,
    pattern_options: Mapping[str, dict[str, Any]] | None = None,
    strategy_options: Mapping[str, Any] | None = None,
    store: Any | None = None,
    run_id: int | None = None,
    validators: Sequence[Any] = (),
    bench_series: PriceSeries | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> ScanReport:
    """Run one strategy across one universe.

    This is the single entry point the CLI, the web UI and the scheduler all
    call, so a scan behaves identically everywhere.
    """
    strategy = STRATEGIES.create(strategy_name)
    opts = dict(strategy_options or {})
    report = ScanReport(strategy=strategy_name, market=market)
    started = time.perf_counter()

    if bench_series is None:
        bench_symbol = feed.index_for(market)
        if bench_symbol:
            try:
                bench_series = feed.price_series(bench_symbol, lookback)
            except Exception:  # noqa: BLE001 - M/L degrade to unknown
                report.notes.append(f"benchmark {bench_symbol} unavailable")

    # --- step 1: prices (whole universe) --------------------------------
    def fetch(sym: str) -> PriceSeries:
        return feed.price_series(sym, lookback)

    def on_err(sym: str, exc: Exception) -> None:
        report.errors += 1

    price_map = scan_prices(
        symbols, fetch, workers=workers, limiter=limiter, on_error=on_err
    )
    report.scanned = len(price_map)

    # --- step 2 + 3: detect, then decide whether to spend ----------------
    pending: list[tuple[str, PriceSeries, dict[str, dict[str, Any]]]] = []
    for sym, series in price_map.items():
        as_of = series.last.date if series.last else ""
        try:
            fired = detect_patterns(series, strategy.requires, pattern_options=pattern_options)
        except Exception as exc:  # noqa: BLE001
            report.errors += 1
            fired = {}

        if not fired:
            report.misses += 1
            report.observations.append(
                {
                    "symbol": sym, "market": series.market, "as_of": as_of,
                    "score": None, "grade": None, "confidence": None,
                    "pattern": None, "pattern_strength": None,
                    "evidence": [], "detail": {"reason": "no pattern"},
                }
            )
            if store is not None:
                store.record_observation(
                    run_id=run_id, symbol=sym, market=series.market, as_of=as_of,
                    score=None, grade=None, confidence=None, pattern=None,
                    pattern_strength=None, evidence=[], detail={"reason": "no pattern"},
                )
            continue
        pending.append((sym, series, fired))

    # --- step 4: fundamentals ONLY for survivors ------------------------
    survivors = [sym for sym, _, _ in pending]
    if strategy.needs_fundamentals and survivors:
        fund_map = scan_fundamentals(
            survivors,
            lambda s: feed.fundamentals(s),
            workers=max(1, workers // 2),
            limiter=limiter,
        )
    else:
        fund_map = {}

    # --- step 5: score + build thesis -----------------------------------
    for sym, series, fired in pending:
        fund = fund_map.get(sym) or Fundamentals(symbol=sym)
        if sym not in fund_map and strategy.needs_fundamentals:
            report.notes.append(f"{sym}: fundamentals unavailable, scored as unknown")
        try:
            evidence, grade, confidence = strategy.score(
                series, fund, patterns=fired, bench_series=bench_series, **opts
            )
            thesis = strategy.thesis(
                series=series, fundamentals=fund, patterns=fired,
                evidence=evidence, grade=grade, confidence=confidence, **opts
            )
            if validators:
                thesis, _results = validate_thesis(thesis, validators)
        except Exception as exc:  # noqa: BLE001
            report.errors += 1
            report.notes.append(f"{sym}: scoring error {type(exc).__name__}: {exc}")
            continue

        report.theses.append(thesis)

        primary = None
        if thesis.pattern and thesis.pattern in fired:
            primary = thesis.pattern
        elif fired:
            primary = next(iter(fired))
        strength = fired.get(primary, {}).get("strength") if primary else None

        obs = {
            "symbol": sym, "market": series.market, "as_of": thesis.as_of,
            "score": float(thesis.confidence), "grade": thesis.grade,
            "confidence": thesis.confidence, "pattern": primary,
            "pattern_strength": strength,
            "evidence": [e.to_dict() for e in thesis.evidence],
            "detail": {"thesis_id": thesis.id},
        }
        report.observations.append(obs)
        if store is not None:
            store.record_observation(
                run_id=run_id, symbol=sym, market=series.market, as_of=thesis.as_of,
                score=float(thesis.confidence), grade=thesis.grade,
                confidence=thesis.confidence, pattern=primary,
                pattern_strength=strength,
                evidence=obs["evidence"], detail=obs["detail"],
            )
            store.save_thesis(thesis, run_id=run_id)

    report.elapsed_s = time.perf_counter() - started
    if on_progress:
        on_progress(report.scanned, report.hits)
    return report


def run(
    *,
    strategy_names: Sequence[str],
    symbols: Sequence[str],
    feed: Any,
    market: str = "IN",
    store: Any | None = None,
    strategy_options: Mapping[str, Mapping[str, Any]] | None = None,
    **kwargs: Any,
) -> dict[str, ScanReport]:
    """Run one or more strategies over the same symbols in one pass.

    Prices and pattern detections are shared: each strategy reuses the price
    series, but re-runs only the detectors it declares in `requires`, so two
    strategies are compared on identical inputs.

    Returns a report per strategy, keyed by strategy name.
    """
    reports: dict[str, ScanReport] = {}
    run_id = None
    if store is not None:
        run_id = store.start_run(
            market=market,
            strategy=",".join(strategy_names),
            plan_total=len(symbols),
        )

    opts = strategy_options or {}
    for name in strategy_names:
        reports[name] = run_strategy(
            name,
            symbols,
            feed=feed,
            market=market,
            store=store,
            run_id=run_id,
            strategy_options=opts.get(name, {}),
            **kwargs,
        )

    if store is not None and run_id is not None:
        scanned = sum(r.scanned for r in reports.values())
        errors = sum(r.errors for r in reports.values())
        hits = sum(r.hits for r in reports.values())
        store.finish_run(run_id, scanned=scanned, errors=errors, hits=hits)

    return reports
