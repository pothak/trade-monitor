"""CAN SLIM strategy: aggregates the seven letters into a graded thesis.

Grading is explicit and inspectable rather than a black box:

  base          100 * (weight passed) / (weight available)
  penalties     failed letters subtract nothing extra (already reflected),
                but unknown letters reduce the *denominator*, which lowers
                confidence rather than grade
  M gate        a failing M caps the grade at C, because O'Neil's own rule is
                not to fight the market
  pattern       the chart pattern contributes a separate strength term; a
                strong pattern cannot rescue failed fundamentals, and strong
                fundamentals alone do not make a tradeable setup

The result is deliberately conservative. Confidence starts from evidence
coverage (how much of the thesis was actually measurable) and is then
adjusted, so a name scored on thin data cannot present as high-conviction.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from trade_monitor_contracts import (
    Evidence,
    Fundamentals,
    Grade,
    Invalidation,
    PriceSeries,
    Thesis,
)
from .canslim import LETTERS, ScoreContext

DEFAULT_WEIGHTS: dict[str, float] = {
    "C": 1.5, "A": 1.0, "N": 1.0, "S": 1.0, "L": 1.0, "M": 1.0, "I": 1.0,
}

GRADE_CUTOFFS: tuple[tuple[float, Grade], ...] = (
    (85.0, "A"), (70.0, "B"), (55.0, "C"), (40.0, "D"), (0.0, "F"),
)


class CanSlimStrategy:
    name = "canslim"
    requires = ("cup_with_handle",)
    needs_fundamentals = True

    def __init__(self, weights: dict[str, float] | None = None) -> None:
        self.weights = {**DEFAULT_WEIGHTS, **(weights or {})}

    # -- scoring ---------------------------------------------------------

    def score_letters(
        self, ctx: ScoreContext
    ) -> list[Evidence]:
        out: list[Evidence] = []
        for key, fn in LETTERS.items():
            try:
                out.append(fn(ctx))
            except Exception as exc:  # noqa: BLE001 - a broken letter must not
                # take down the whole scan; it becomes an unknown with the
                # reason attached.
                out.append(
                    Evidence(key, key, None, None, "unknown",
                             f"scorer error: {type(exc).__name__}: {exc}",
                             {"error": True})
                )
        return out

    def score(
        self,
        series: PriceSeries,
        fundamentals: Fundamentals,
        patterns: Mapping[str, dict[str, Any]] | None = None,
        **options: Any,
    ) -> tuple[list[Evidence], Grade, int]:
        bench = options.pop("bench_series", None)
        # A pattern strength passed explicitly wins; otherwise take it from the
        # primary pattern the engine detected.
        if "pattern_strength" not in options:
            primary = self.primary_pattern(patterns)
            if primary is not None:
                options["pattern_strength"] = primary.get("strength")
        ctx = ScoreContext(
            symbol=series.symbol,
            market=series.market,
            series=series,
            fundamentals=fundamentals,
            bench_series=bench,
            options=options,
        )
        evidence = self.score_letters(ctx)
        grade, confidence = self._grade(evidence, options)
        return evidence, grade, confidence

    def primary_pattern(
        self, patterns: Mapping[str, dict[str, Any]] | None
    ) -> dict[str, Any] | None:
        """The pattern this strategy tells its story around.

        When several of the required patterns fired, prefer the one with the
        highest strength so the rendered thesis matches the strongest evidence.
        """
        if not patterns:
            return None
        candidates = [patterns[n] for n in self.requires if n in patterns]
        if not candidates:
            candidates = list(patterns.values())
        if not candidates:
            return None
        return max(candidates, key=lambda g: g.get("strength", 0.0))

    def _grade(
        self, evidence: Sequence[Evidence], options: dict[str, Any]
    ) -> tuple[Grade, int]:
        earned = 0.0
        available = 0.0
        unknown_weight = 0.0
        for ev in evidence:
            w = self.weights.get(ev.key, 1.0)
            if ev.verdict == "unknown":
                # Excluded from the denominator: it lowers confidence via
                # coverage, and therefore cannot inflate the grade.
                unknown_weight += w
                continue
            available += w
            if ev.verdict == "pass":
                earned += w

        base = (earned / available * 100.0) if available else 0.0

        pattern_strength = options.get("pattern_strength")
        if pattern_strength is not None:
            bonus = float(pattern_strength) * options.get("pattern_bonus_weight", 8.0)
            base = min(100.0, base + bonus)

        # O'Neil: do not fight a falling market.
        m = next((e for e in evidence if e.key == "M"), None)
        if m is not None and m.verdict == "fail":
            base = min(base, 54.0)  # caps grade at C

        grade = "F"
        for cutoff, letter in GRADE_CUTOFFS:
            if base >= cutoff:
                grade = letter
                break

        # Confidence blends how good the evidence is with how complete it is.
        total_w = sum(self.weights.get(e.key, 1.0) for e in evidence)
        coverage = (total_w - unknown_weight) / total_w if total_w else 0.0
        confidence = int(round(base * (0.55 + 0.45 * coverage)))
        return grade, max(0, min(100, confidence))

    # -- thesis ----------------------------------------------------------

    def thesis(
        self,
        *,
        series: PriceSeries,
        fundamentals: Fundamentals,
        patterns: Mapping[str, dict[str, Any]] | None = None,
        evidence: Sequence[Evidence],
        grade: Grade,
        confidence: int,
        name: str | None = None,
        **options: Any,
    ) -> Thesis:
        pattern = self.primary_pattern(patterns) if patterns is not None else None
        last = series.last
        as_of = last.date if last else datetime.now(timezone.utc).date().isoformat()

        degraded = list(dict.fromkeys(fundamentals.degraded))
        if pattern is None:
            degraded.append("no chart pattern detected")

        invalidation = self._invalidation(pattern, last.close if last else None)

        thesis_id = hashlib.sha256(
            f"{series.symbol}|{self.name}|{as_of}|{grade}|{confidence}".encode()
        ).hexdigest()[:16]

        thesis = Thesis(
            id=thesis_id,
            symbol=series.symbol,
            name=name or series.symbol,
            market=series.market,
            currency=series.currency,
            as_of=as_of,
            strategy=self.name,
            grade=grade,
            confidence=confidence,
            evidence=list(evidence),
            invalidation=invalidation,
            measured_move_target=(pattern or {}).get("measured_move_target"),
            pattern=(pattern or {}).get("pattern"),
            pattern_detail=pattern or {},
            degraded_inputs=degraded,
        )
        object.__setattr__(thesis, "narrative", self.narrative(thesis))
        return thesis

    def build_thesis(self, *, pattern: dict[str, Any] | None = None, **kwargs: Any) -> Thesis:
        """Back-compat shim for the pre-patterns-dict signature."""
        patterns = {"_": pattern} if pattern else None
        return self.thesis(patterns=patterns, **kwargs)

    @staticmethod
    def _invalidation(
        pattern: dict[str, Any] | None, last_close: float | None
    ) -> Invalidation:
        if not pattern:
            return Invalidation(
                None,
                "no pattern established; thesis requires a fresh setup",
                None,
            )
        stop = pattern.get("stop_reference")
        rule = f"close below {stop:.2f} invalidates the {pattern['pattern']} base" if stop else ""
        pivot = pattern.get("pivot_price")
        if pivot and last_close:
            rule += (
                f"; no buy until price clears pivot {pivot:.2f} "
                f"({pattern.get('distance_to_pivot_pct', 0):.1f}% away)"
            )
        return Invalidation(
            stop_price=stop, rule=rule,
            time_stop_days=pattern.get("time_stop_days", 60),
        )

    def narrative(self, thesis: Thesis) -> str:
        lines = [f"{thesis.symbol} — grade {thesis.grade}, confidence {thesis.confidence}"]
        passed = [e.key for e in thesis.passed]
        failed = [e.key for e in thesis.failed]
        unknown = [e.key for e in thesis.unknown]
        if passed:
            lines.append(f"Passing: {' '.join(passed)}")
        if failed:
            lines.append(f"Failing: {' '.join(failed)}")
        if unknown:
            lines.append(f"Unverified: {' '.join(unknown)}")
        if thesis.pattern:
            d = thesis.pattern_detail
            lines.append(
                f"{thesis.pattern.replace('_', ' ')}: {d.get('cup_depth_pct', 0):.1f}% deep, "
                f"{d.get('cup_bars', 0)}d cup, {d.get('handle_bars', 0)}d handle, "
                f"pivot {d.get('pivot_price', 0):.2f}, "
                f"{d.get('distance_to_pivot_pct', 0):.1f}% to pivot"
            )
        if thesis.invalidation.rule:
            lines.append(f"Invalidation: {thesis.invalidation.rule}")
        if thesis.degraded_inputs:
            lines.append(f"Data gaps: {'; '.join(thesis.degraded_inputs)}")
        return "\n".join(lines)