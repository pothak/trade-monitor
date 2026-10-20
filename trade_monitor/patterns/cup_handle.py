"""Cup-with-handle detection.

O'Neil-style geometry expressed as explicit numeric criteria, so every
rejection is explainable rather than opaque.

Approach: a cup is anchored on a *local* rim high, not the global high of the
window. Anchoring on the window maximum picks whatever recent peak happens to be
tallest, which produces tiny 5% "cups" and misses real bases. So we iterate
candidate rims newest-first, build the geometry for each, gate on structure,
and keep the best-scoring candidate.

  Cup (rounded base)
    - depth 10-33% from rim high to base low
    - duration 25-65 sessions
    - rounded floor: meaningful time spent near the low, and a recovery phase
      that is slower than the decline (V bottoms fail this)

  Handle
    - 5-30 sessions, 0-15% off the rim
    - volume continues to contract (the tell)

  Pivot
    - rim high, with tolerance for noise

A detection is a *candidate*: geometry present, not breakout confirmed.
Confidence and invalidation are computed separately by the strategy layer.
"""

from __future__ import annotations

from typing import Any

from trade_monitor_contracts import PriceSeries


def _pct_drop(a: float, b: float) -> float:
    return (a - b) / a * 100.0 if a else 0.0


class CupWithHandle:
    name = "cup_with_handle"

    DEFAULTS: dict[str, Any] = {
        "min_cup_depth_pct": 10.0,
        "max_cup_depth_pct": 33.0,
        "min_cup_bars": 25,
        "max_cup_bars": 65,
        "min_handle_bars": 5,
        "max_handle_bars": 30,
        "max_handle_depth_pct": 15.0,
        "max_handle_retrace_frac": 0.50,
        "pivot_tolerance_pct": 2.0,
        "volume_dryup_ratio": 0.95,
        "min_flat_base_frac": 0.10,
        "flat_zone_pct": 15.0,
        "min_base_lift_frac": 0.50,
        "rim_neighbours": 5,
        "max_candidates": 40,
    }

    def detect(self, series: PriceSeries, **options: Any) -> dict[str, Any] | None:
        o = {**self.DEFAULTS, **options}
        bars = series.bars
        n = len(bars)
        if n < 60:
            return None

        highs = [b.high for b in bars]
        lows = [b.low for b in bars]
        closes = [b.close for b in bars]
        vols = [b.volume for b in bars]

        if not any(v > 0 for v in vols):
            return None

        # Newest-first rim candidates: a local high versus a lookback window on
        # each side, and only rims far enough back to leave room for a cup.
        k = o["rim_neighbours"]
        lo_rim = o["min_cup_bars"] + o["min_handle_bars"]
        hi_rim = n - o["min_handle_bars"] - 1
        rims: list[int] = []
        for i in range(max(lo_rim, k), min(hi_rim, n - k - 1)):
            left = max(0, i - k)
            right = min(n, i + k + 1)
            if highs[i] >= max(highs[left:i] or [highs[i]]) and highs[i] > max(highs[i + 1 : right] or [0]):
                rims.append(i)
        rims = rims[-o["max_candidates"] :][::-1]
        if not rims:
            return None

        best: dict[str, Any] | None = None
        for rim_idx in rims:
            geom = self._geometry(rim_idx, bars, highs, lows, closes, vols, o)
            if geom is None:
                continue
            if best is None or geom["strength"] > best["strength"]:
                best = geom
        return best

    # -- geometry for one candidate rim ---------------------------------

    def _geometry(
        self,
        rim_idx: int,
        bars,
        highs: list[float],
        lows: list[float],
        closes: list[float],
        vols: list[float],
        o: dict[str, Any],
    ) -> dict[str, Any] | None:
        n = len(bars)
        rim_high = highs[rim_idx]
        if rim_high <= 0:
            return None

        # Clamp rather than reject: a rim near the end of the data still has a
        # valid cup+handle if the bars that exist satisfy the criteria.
        cup_end = min(n, rim_idx + o["max_cup_bars"] + o["max_handle_bars"])

        cup_lo = lows[rim_idx + 1 : cup_end + 1]
        if not cup_lo:
            return None
        base_rel = cup_lo.index(min(cup_lo))
        base_idx = rim_idx + 1 + base_rel
        base_low = lows[base_idx]

        # PAT-6: the low must be a *confirmed trough*, not merely the minimum in
        # a truncated window. On a still-forming base the running low sits at
        # the very end of the data, so anchoring there mislabels an unfinished
        # decline as a cup base. Require at least one bar of lift off the low,
        # so the base is something price has actually left.
        if base_idx >= n - 1:
            return None
        if highs[base_idx + 1] < base_low:  # price still making new lows
            return None

        depth_pct = _pct_drop(rim_high, base_low)
        cup_bars = base_idx - rim_idx

        criteria: list[tuple[str, bool, str]] = [
            (
                "cup_depth",
                o["min_cup_depth_pct"] <= depth_pct <= o["max_cup_depth_pct"],
                f"{depth_pct:.1f}% vs {o['min_cup_depth_pct']}-{o['max_cup_depth_pct']}%",
            ),
            (
                "cup_duration",
                o["min_cup_bars"] <= cup_bars <= o["max_cup_bars"],
                f"{cup_bars} sessions vs {o['min_cup_bars']}-{o['max_cup_bars']}",
            ),
        ]

        # A handle is not "whatever happened after the low". It is a shallow
        # pullback that begins only after price recovers out of the base, and
        # it retraces a small fraction of the cup depth. Anchoring the handle
        # at the low itself mislabels an unfinished decline as a handle.
        mid_level = base_low + 0.5 * (rim_high - base_low)
        recovery_idx = None
        for j in range(base_idx + 1, n):
            if highs[j] >= mid_level:
                recovery_idx = j
                break

        handle_start_idx = recovery_idx if recovery_idx is not None else base_idx + 1
        recovered = highs[handle_start_idx:] if handle_start_idx < n else []
        handle_high = max(recovered) if recovered else rim_high
        handle_low = min(lows[handle_start_idx:]) if handle_start_idx < n else lows[-1]
        handle_bars_eff = n - handle_start_idx

        handle_depth_pct = _pct_drop(rim_high, handle_low)
        cup_depth_amt = rim_high - base_low
        handle_retrace_frac = (
            (handle_high - handle_low) / cup_depth_amt if cup_depth_amt > 0 else 0.0
        )

        criteria.append(
            (
                "handle_recovered",
                recovery_idx is not None,
                f"recovered to {100 * (1 - _pct_drop(rim_high, mid_level)):.0f}% of rim"
                if recovery_idx is not None
                else "no recovery above mid-cup",
            )
        )
        criteria.append(
            (
                "handle_depth",
                0.0 < handle_depth_pct <= o["max_handle_depth_pct"],
                f"{handle_depth_pct:.1f}% off rim vs max {o['max_handle_depth_pct']}%",
            )
        )
        criteria.append(
            (
                "handle_shallow_vs_cup",
                handle_retrace_frac <= o["max_handle_retrace_frac"],
                f"retraces {handle_retrace_frac:.0%} of cup depth vs max {o['max_handle_retrace_frac']:.0%}",
            )
        )

        handle_bars = max(handle_bars_eff, 1)
        criteria.insert(
            2,
            (
                "handle_duration",
                o["min_handle_bars"] <= handle_bars <= o["max_handle_bars"],
                f"{handle_bars} sessions vs {o['min_handle_bars']}-{o['max_handle_bars']}",
            ),
        )

        # Every structural criterion must hold for a pattern to exist at all.
        # Volume and roundedness tune strength; they do not create the pattern.
        structural = {
            "cup_depth",
            "cup_duration",
            "handle_duration",
            "handle_recovered",
            "handle_depth",
            "handle_shallow_vs_cup",
        }
        if not all(ok for key, ok, _ in criteria if key in structural):
            return None

        # Rounded floor: time spent near the low, plus a recovery phase at
        # least as long as the decline. A V bottom fails the recovery test.
        # "Rounded" is measured as time spent near the low, not by comparing against
        # the handle (a handle is shallow by definition, so that ratio carries
        # no information about the floor's shape).
        zone = base_low * (1 + o["flat_zone_pct"] / 100.0)
        flat_bars = sum(1 for j in range(rim_idx, base_idx + 1) if lows[j] <= zone)
        flat_frac = flat_bars / cup_bars if cup_bars else 0.0

        # The right side of the cup must lift off the low. If the handle starts
        # at the base with no intervening recovery, the shape is a V or a
        # straight decline and the cup is not yet formed.
        if recovery_idx is not None:
            lift = (highs[recovery_idx] - base_low) / (rim_high - base_low)
        else:
            lift = 0.0

        criteria.append(
            (
                "rounded_base",
                flat_frac >= o["min_flat_base_frac"] and lift >= o["min_base_lift_frac"],
                f"flat {flat_frac:.2f}, lift to {lift:.0%} of rim",
            )
        )

        # Volume contraction into the base and through the handle.
        half = max(3, cup_bars // 2)
        cup_first = sum(vols[rim_idx : rim_idx + half]) / half
        cup_second = sum(vols[base_idx : base_idx + half]) / half
        cup_dry = (cup_second / cup_first) if cup_first else 1.0
        hv_start = recovery_idx if recovery_idx is not None else base_idx + 1
        handle_vol = sum(vols[hv_start:]) / max(1, handle_bars)
        handle_dry = (handle_vol / cup_second) if cup_second else 1.0
        criteria.append(
            (
                "volume_dryup_cup",
                cup_dry <= o["volume_dryup_ratio"],
                f"{cup_dry:.2f}x",
            )
        )
        criteria.append(
            (
                "volume_dryup_handle",
                handle_dry <= o["volume_dryup_ratio"],
                f"{handle_dry:.2f}x",
            )
        )

        passed = sum(1 for _, ok, _ in criteria if ok)
        strength = round(passed / len(criteria), 3)
        pivot = rim_high * (1 + o["pivot_tolerance_pct"] / 100.0)
        depth = rim_high - base_low

        return {
            "pattern": "cup_with_handle",
            "detected": True,
            "bars_in_data": n,
            "rim_index": rim_idx,
            "rim_date": bars[rim_idx].date,
            "rim_high": round(rim_high, 4),
            "base_index": base_idx,
            "base_date": bars[base_idx].date,
            "base_low": round(base_low, 4),
            "cup_bars": cup_bars,
            "cup_depth_pct": round(depth_pct, 2),
            "handle_start_date": bars[handle_start_idx].date,
            "handle_bars": handle_bars,
            "handle_depth_pct": round(handle_depth_pct, 2),
            "handle_retrace_frac": round(handle_retrace_frac, 3),
            "handle_low": round(handle_low, 4),
            "handle_high": round(handle_high, 4),
            "flat_base_frac": round(flat_frac, 3),
            "base_lift_frac": round(lift, 3),
            "cup_volume_ratio": round(cup_dry, 3),
            "handle_volume_ratio": round(handle_dry, 3),
            "pivot_price": round(pivot, 4),
            "last_close": round(closes[-1], 4),
            "distance_to_pivot_pct": round(_pct_drop(pivot, closes[-1]), 2),
            "measured_move_target": round(pivot + depth, 4),
            "stop_reference": round(base_low, 4),
            "strength": strength,
            "criteria": [
                {"criterion": c, "passed": ok, "detail": d} for c, ok, d in criteria
            ],
        }


REGISTRY: dict[str, CupWithHandle] = {"cup_with_handle": CupWithHandle()}

__all__ = ["CupWithHandle", "REGISTRY"]
