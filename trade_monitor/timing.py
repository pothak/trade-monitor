"""Per-stage timing, so "is it fast?" is a number, not a feeling.

Every scan records where its time went. The point is not to make scans fast by
decree; it is to make the *next* optimization decision from data. See
docs/PERFORMANCE.md, which records what the numbers have shown so far (network
dominates by ~1000x; compute is a rounding error).

Usage:

    from trade_monitor.timing import Stages

    st = Stages()
    with st.time("fetch"):
        ...
    with st.time("detect"):
        ...
    st.summary()   # {'fetch': 2.38, 'detect': 0.001, 'total': 2.39}

A stage can be entered many times (e.g. once per symbol) and the times
accumulate, so a scan with 2,500 symbols still produces one row per stage.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Stages:
    """Accumulating per-stage timer."""

    seconds: dict[str, float] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    _start: float | None = None

    @contextmanager
    def time(self, stage: str) -> Iterator[None]:
        if stage not in self.seconds:
            self.seconds[stage] = 0.0
            self.counts[stage] = 0
            self.order.append(stage)
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.seconds[stage] += time.perf_counter() - t0
            self.counts[stage] += 1

    def add(self, stage: str, seconds: float, count: int = 1) -> None:
        if stage not in self.seconds:
            self.seconds[stage] = 0.0
            self.counts[stage] = 0
            self.order.append(stage)
        self.seconds[stage] += seconds
        self.counts[stage] += count

    def total(self) -> float:
        return sum(self.seconds.values())

    def summary(self) -> dict[str, Any]:
        total = self.total() or 1e-9
        rows = []
        for stage in self.order:
            secs = self.seconds[stage]
            rows.append({
                "stage": stage,
                "seconds": round(secs, 6),
                "count": self.counts[stage],
                "per_call_ms": round(secs / self.counts[stage] * 1000, 4)
                if self.counts[stage] else None,
                "share_pct": round(secs / total * 100, 3),
            })
        rows.sort(key=lambda r: r["seconds"], reverse=True)
        return {
            "total_s": round(self.total(), 6),
            "stages": rows,
            "network_share_pct": round(
                _share(self, ("fetch", "fundamentals", "benchmark")), 3
            ),
            "compute_share_pct": round(_share(self, ("detect", "score")), 3),
        }

    def report(self) -> str:
        s = self.summary()
        lines = [f"total {s['total_s']:.3f}s  "
                 f"(network {s['network_share_pct']:.1f}%, "
                 f"compute {s['compute_share_pct']:.1f}%)"]
        for row in s["stages"]:
            bar = "█" * max(1, int(row["share_pct"] / 2))
            lines.append(
                f"  {row['stage']:14s} {row['seconds']*1000:9.1f} ms  "
                f"{row['share_pct']:6.2f}%  x{row['count']:<6d} {bar}"
            )
        return "\n".join(lines)


def _share(stages: Stages, names: tuple[str, ...]) -> float:
    total = stages.total() or 1e-9
    return sum(stages.seconds.get(n, 0.0) for n in names) / total * 100


__all__ = ["Stages"]
