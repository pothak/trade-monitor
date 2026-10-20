"""A mean-reversion reference strategy.

The third free example, and the second proof that the framework has no opinion.
Where CAN SLIM and `momentum_rank` both reward strength, this one rewards
*weakness*: it looks for names that have fallen hard and may snap back.

CAN SLIM        buys strength off a base
momentum_rank   buys strength already moving
mean_reversion  buys weakness, expecting a bounce

All three run on the same engine, the same data, the same audit trail. Nothing
in the framework prefers one. That is the whole point of a strategy host.

Price-only, so it costs zero fundamental fetches.
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


class MeanReversion:
    """Buy oversold, with a stability check to avoid falling knives.

    Three checks:

      drawdown   price has fallen a meaningful amount from its recent high
      oversold   price is below its short moving average (stretched down)
      stability  the last bar is not a new low (the fall is slowing)

    The stability check is what separates "oversold" from "still crashing".
    """

    name = "mean_reversion"
    version = "0.1.0"
    requires = ()
    needs_fundamentals = False

    def __init__(
        self,
        *,
        high_window: int = 120,
        min_drawdown_pct: float = 20.0,
        ma_window: int = 10,
    ) -> None:
        self.high_window = high_window
        self.min_drawdown_pct = min_drawdown_pct
        self.ma_window = ma_window

    def score(
        self,
        series: PriceSeries,
        fundamentals: Fundamentals,
        patterns: Mapping[str, dict[str, Any]] | None = None,
        **options: Any,
    ) -> tuple[list[Evidence], Grade, int]:
        bars = series.bars
        closes = [b.close for b in bars]
        evidence: list[Evidence] = []

        # --- drawdown from the recent high ------------------------------
        if len(closes) >= 20:
            window = closes[-self.high_window:]
            peak = max(window)
            dd = (peak - closes[-1]) / peak * 100 if peak else None
        else:
            dd = None
        if dd is None:
            evidence.append(Evidence(
                "drawdown", "Drawdown from high", None, self.min_drawdown_pct,
                "unknown", "insufficient history",
            ))
        else:
            ok = dd >= self.min_drawdown_pct
            evidence.append(Evidence(
                "drawdown", "Drawdown from high", round(dd, 2),
                self.min_drawdown_pct, "pass" if ok else "fail",
                f"{dd:.1f}% below recent high "
                f"(want >= {self.min_drawdown_pct}%)",
            ))

        # --- oversold vs short moving average ---------------------------
        if len(closes) >= self.ma_window:
            ma = sum(closes[-self.ma_window:]) / self.ma_window
            below = closes[-1] < ma
            evidence.append(Evidence(
                "oversold", f"Below {self.ma_window}d MA", round(closes[-1], 2),
                round(ma, 2), "pass" if below else "fail",
                f"last {closes[-1]:.2f} vs MA {ma:.2f}",
            ))
        else:
            evidence.append(Evidence(
                "oversold", f"Below {self.ma_window}d MA", None, None,
                "unknown", "insufficient history",
            ))

        # --- stability: not making a fresh low --------------------------
        if len(bars) >= 3:
            lows = [b.low for b in bars[-20:]]
            is_new_low = bars[-1].low <= min(lows[:-1]) if len(lows) > 1 else False
            evidence.append(Evidence(
                "stability", "Not a fresh low", round(bars[-1].low, 2),
                round(min(lows[:-1]), 2) if len(lows) > 1 else None,
                "fail" if is_new_low else "pass",
                "new low: fall still accelerating" if is_new_low
                else "holding above recent low",
            ))
        else:
            evidence.append(Evidence(
                "stability", "Not a fresh low", None, None,
                "unknown", "insufficient history",
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
        stop = round(closes[-1] * 0.90, 2) if closes else None

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
                rule="reversion failed: close 10% below entry, or a new low",
                time_stop_days=20,
            ),
            pattern="mean_reversion",
            pattern_detail={},
            degraded_inputs=["price-only strategy: fundamentals not used"],
        )
        object.__setattr__(thesis, "narrative", self.narrative(thesis))
        return thesis

    def narrative(self, thesis: Thesis) -> str:
        return (
            f"{thesis.symbol} mean-reversion {thesis.grade} "
            f"(confidence {thesis.confidence})\n"
            f"passing: {' '.join(e.key for e in thesis.passed) or '-'}\n"
            f"failing: {' '.join(e.key for e in thesis.failed) or '-'}"
        )


STRATEGY = MeanReversion()
