"""A minimal reference strategy, to demonstrate runtime injection.

This file is *not* imported by the package. It is discovered by
`canslim.strategy.loader.discover()` when it scans `strategies/`, which proves
that a strategy can be dropped in without touching the monitor.

It scores a chart base on price action alone -- no fundamentals at all -- which
also demonstrates `needs_fundamentals = False`: the engine will skip the
expensive fundamentals fetch entirely for names scored by this strategy.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from trade_monitor_contracts import (
    Evidence,
    Fundamentals,
    Grade,
    Invalidation,
    PriceSeries,
    Thesis,
)


class BreakoutProximity:
    """Price-only setup detector.

    Grades how close a name is to clearing a recent high with volume support.
    Deliberately simple: it exists to prove the injection seam, not to trade.
    """

    name = "breakout_proximity"
    version = "0.1.0"
    requires = ("cup_with_handle",)
    needs_fundamentals = False

    def __init__(
        self,
        *,
        proximity_pct: float = 5.0,
        volume_confirm_ratio: float = 1.0,
    ) -> None:
        self.proximity_pct = proximity_pct
        self.volume_confirm_ratio = volume_confirm_ratio

    def score(
        self,
        series: PriceSeries,
        fundamentals: Fundamentals,
        patterns: Mapping[str, dict[str, Any]] | None = None,
        **options: Any,
    ) -> tuple[list[Evidence], Grade, int]:
        bars = series.bars
        evidence: list[Evidence] = []

        pivot = None
        if patterns:
            first = next(iter(patterns.values()))
            pivot = first.get("pivot_price")

        last = bars[-1].close if bars else 0.0
        if pivot and last:
            dist = (pivot - last) / pivot * 100
        else:
            dist = None

        if dist is None:
            evidence.append(Evidence(
                "proximity", "Distance to pivot", None, self.proximity_pct,
                "unknown", "no pivot available",
            ))
        else:
            ok = 0 <= dist <= self.proximity_pct
            evidence.append(Evidence(
                "proximity", "Distance to pivot", round(dist, 2), self.proximity_pct,
                "pass" if ok else "fail",
                f"{dist:.2f}% from pivot (want <= {self.proximity_pct}%)",
            ))

        if len(bars) >= 20:
            recent = sum(b.volume for b in bars[-5:]) / 5
            base = sum(b.volume for b in bars[-20:-5]) / 15
            ratio = recent / base if base else None
        else:
            ratio = None

        if ratio is None:
            evidence.append(Evidence(
                "volume", "Volume support", None, self.volume_confirm_ratio,
                "unknown", "insufficient volume history",
            ))
        else:
            ok = ratio <= self.volume_confirm_ratio
            evidence.append(Evidence(
                "volume", "Volume support", round(ratio, 3),
                self.volume_confirm_ratio, "pass" if ok else "fail",
                f"recent volume {ratio:.2f}x baseline",
            ))

        passed = sum(1 for e in evidence if e.verdict == "pass")
        measured = [e for e in evidence if e.verdict != "unknown"]
        coverage = len(measured) / len(evidence) if evidence else 0.0
        base_score = passed / len(measured) * 100 if measured else 0.0
        confidence = int(round(base_score * (0.55 + 0.45 * coverage)))

        if base_score >= 100:
            grade: Grade = "A"
        elif base_score >= 50:
            grade = "B"
        else:
            grade = "D"

        return evidence, grade, confidence

    def thesis(
        self,
        *,
        series: PriceSeries,
        fundamentals: Fundamentals,
        patterns: Mapping[str, dict[str, Any]] | None = None,
        evidence: Sequence[Evidence],
        grade: Grade,
        confidence: int,
        **options: Any,
    ) -> Thesis:
        import hashlib

        last = series.last
        as_of = last.date if last else ""
        pattern = next(iter(patterns.values())) if patterns else None
        tid = hashlib.sha256(
            f"{series.symbol}|{self.name}|{as_of}|{grade}|{confidence}".encode()
        ).hexdigest()[:16]

        thesis = Thesis(
            id=tid,
            symbol=series.symbol,
            name=series.symbol,
            market=series.market,
            currency=series.currency,
            as_of=as_of,
            strategy=self.name,
            grade=grade,
            confidence=confidence,
            evidence=list(evidence),
            invalidation=Invalidation(
                stop_price=(pattern or {}).get("stop_reference"),
                rule="injected reference strategy: price-only invalidation",
                time_stop_days=60,
            ),
            measured_move_target=(pattern or {}).get("measured_move_target"),
            pattern=(pattern or {}).get("pattern"),
            pattern_detail=pattern or {},
            degraded_inputs=["price-only strategy: fundamentals not used"],
        )
        object.__setattr__(thesis, "narrative", self.narrative(thesis))
        return thesis

    def narrative(self, thesis: Thesis) -> str:
        passed = [e.key for e in thesis.passed]
        failed = [e.key for e in thesis.failed]
        return (
            f"{thesis.symbol} [injected] grade {thesis.grade}, confidence "
            f"{thesis.confidence}\npassing: {' '.join(passed) or '-'}\n"
            f"failing: {' '.join(failed) or '-'}"
        )


# Discovered by the loader.
STRATEGY = BreakoutProximity()
