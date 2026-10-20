"""A momentum-ranking reference strategy.

Shipped free with trade-monitor to show that the framework has **no market
opinion**. This one is philosophically the opposite of CAN SLIM:

  - no fundamentals at all        (needs_fundamentals = False)
  - no chart patterns            (requires = ())
  - it rewards *strength*, not bases ready to break out

CAN SLIM looks for a quiet base about to move. Momentum looks for a name
already moving. Both are legitimate; the engine does not care which you prefer.
That is the point: bring your own idea.

It is discovered at runtime from `strategies/`, exactly like any user plugin.
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping, Sequence

from trade_monitor_contracts import (
    Evidence,
    Fundamentals,
    Grade,
    Invalidation,
    PriceSeries,
    Thesis,
)


class MomentumRank:
    """Rank by rate of change, confirmed by trend and volume.

    Three checks, deliberately simple and transparent:

      momentum   price change over `lookback`, higher is better
      trend      price above its long moving average
      volume     recent volume at least as heavy as baseline

    Grades A when everything lines up, F when nothing does.
    """

    name = "momentum_rank"
    version = "0.1.0"
    requires = ()            # no pattern detector needed
    needs_fundamentals = False  # price-only: zero expensive fetches

    def __init__(
        self,
        *,
        lookback: int = 60,
        trend_window: int = 50,
        min_momentum_pct: float = 10.0,
        volume_ratio: float = 1.0,
    ) -> None:
        self.lookback = lookback
        self.trend_window = trend_window
        self.min_momentum_pct = min_momentum_pct
        self.volume_ratio = volume_ratio

    def score(
        self,
        series: PriceSeries,
        fundamentals: Fundamentals,
        patterns: Mapping[str, dict[str, Any]] | None = None,
        **options: Any,
    ) -> tuple[list[Evidence], Grade, int]:
        bars = series.bars
        evidence: list[Evidence] = []
        closes = [b.close for b in bars]

        # --- momentum: total return over the lookback -------------------
        if len(closes) > self.lookback:
            past = closes[-self.lookback - 1]
            roc = (closes[-1] / past - 1) * 100 if past else None
        else:
            roc = None
        if roc is None:
            evidence.append(Evidence(
                "momentum", f"{self.lookback}d momentum", None,
                self.min_momentum_pct, "unknown", "insufficient history",
            ))
        else:
            ok = roc >= self.min_momentum_pct
            evidence.append(Evidence(
                "momentum", f"{self.lookback}d momentum", round(roc, 2),
                self.min_momentum_pct, "pass" if ok else "fail",
                f"{roc:+.1f}% over {self.lookback} bars "
                f"(want >= {self.min_momentum_pct}%)",
            ))

        # --- trend: price vs long moving average ------------------------
        if len(closes) >= self.trend_window:
            ma = sum(closes[-self.trend_window:]) / self.trend_window
            above = closes[-1] > ma
            evidence.append(Evidence(
                "trend", f"Above {self.trend_window}d MA", round(closes[-1], 2),
                round(ma, 2), "pass" if above else "fail",
                f"last {closes[-1]:.2f} vs MA {ma:.2f}",
            ))
        else:
            evidence.append(Evidence(
                "trend", f"Above {self.trend_window}d MA", None, None,
                "unknown", "insufficient history",
            ))

        # --- volume: confirming participation ---------------------------
        if len(bars) >= 20:
            recent = sum(b.volume for b in bars[-5:]) / 5
            base = sum(b.volume for b in bars[-20:-5]) / 15
            ratio = recent / base if base else None
        else:
            ratio = None
        if ratio is None:
            evidence.append(Evidence(
                "volume", "Volume expansion", None, self.volume_ratio,
                "unknown", "insufficient volume history",
            ))
        else:
            ok = ratio >= self.volume_ratio
            evidence.append(Evidence(
                "volume", "Volume expansion", round(ratio, 3), self.volume_ratio,
                "pass" if ok else "fail", f"recent volume {ratio:.2f}x baseline",
            ))

        return self._grade(evidence)

    def _grade(self, evidence: list[Evidence]) -> tuple[list[Evidence], Grade, int]:
        measured = [e for e in evidence if e.verdict != "unknown"]
        passed = sum(1 for e in evidence if e.verdict == "pass")
        coverage = len(measured) / len(evidence) if evidence else 0.0
        score = passed / len(measured) * 100 if measured else 0.0
        confidence = int(round(score * (0.55 + 0.45 * coverage)))
        if score >= 100:
            grade: Grade = "A"
        elif score >= 66:
            grade = "B"
        elif score >= 33:
            grade = "C"
        else:
            grade = "F"
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
        last = series.last
        as_of = last.date if last else ""
        tid = hashlib.sha256(
            f"{series.symbol}|{self.name}|{as_of}|{grade}|{confidence}".encode()
        ).hexdigest()[:16]

        closes = [b.close for b in series.bars]
        stop = round(closes[-1] * 0.92, 2) if closes else None

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
                stop_price=stop,
                rule="momentum reversal: close below 92% of last price",
                time_stop_days=40,
            ),
            pattern="momentum",
            pattern_detail={},
            degraded_inputs=["price-only strategy: fundamentals not used"],
        )
        object.__setattr__(thesis, "narrative", self.narrative(thesis))
        return thesis

    def narrative(self, thesis: Thesis) -> str:
        return (
            f"{thesis.symbol} momentum {thesis.grade} "
            f"(confidence {thesis.confidence})\n"
            f"passing: {' '.join(e.key for e in thesis.passed) or '-'}\n"
            f"failing: {' '.join(e.key for e in thesis.failed) or '-'}"
        )


STRATEGY = MomentumRank()
