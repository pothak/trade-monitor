"""CAN SLIM scoring.

Seven letters, each an independent scorer that reads normalized data and
returns Evidence. The strategy aggregates them into a grade. Nothing here
touches the network or the store, so it is fully unit-testable offline.

  C  Current quarterly earnings growth, with acceleration
  A  Annual earnings growth over 3 years
  N  New: price at/near a new high (product news is not in the data we have)
  S  Supply and demand: volume behaviour, float
  L  Leader: relative strength versus the market benchmark
  M  Market direction: is the market confirming?
  I  Institutional sponsorship (US: holder data. IN: unavailable.)

Honest limits, stated rather than papered over:
  - N is price-new-high, not news. The "new product" half of the letter is
    not observable from OHLCV+EPS, so N is scored as a price proxy and
    labeled as such in the evidence.
  - I is unavailable for India with the free data we have. The scorer returns
    `unknown`, never a fabricated pass, and the thesis is marked degraded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from trade_monitor_contracts import Evidence, Fundamentals, Grade, Market, PriceSeries


@dataclass
class ScoreContext:
    """Everything a letter may need, resolved once per scan."""

    symbol: str
    market: Market
    series: PriceSeries
    fundamentals: Fundamentals
    bench_series: PriceSeries | None = None
    options: dict[str, Any] = field(default_factory=dict)

    def opt(self, key: str, default: Any) -> Any:
        return self.options.get(key, default)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _yoy_growth(curr: float, prior: float) -> float | None:
    if prior == 0:
        return None
    return (curr - prior) / abs(prior) * 100.0


def _match_year_ago(quarters: Sequence, quarter) -> float | None:
    """Find EPS for the same fiscal quarter one year earlier.

    Indian quarterly series have gaps, so we match on fiscal period rather than
    assuming the series is contiguous. Matching year and quarter is the only
    safe comparison.
    """
    from datetime import date

    d = date.fromisoformat(quarter.period_end)
    want_year = d.year - 1
    want_q = (d.month - 1) // 3 + 1
    for q in quarters:
        qd = date.fromisoformat(q.period_end)
        qn = (qd.month - 1) // 3 + 1
        if qd.year == want_year and qn == want_q:
            return q.eps
    return None


# --------------------------------------------------------------------------
# C - current quarterly earnings
# --------------------------------------------------------------------------


def score_c(ctx: ScoreContext) -> Evidence:
    thresh = ctx.opt("c_min_growth_pct", 20.0)
    accel_thresh = ctx.opt("c_min_accel_pct", 20.0)
    qs = ctx.fundamentals.quarterly_eps
    if len(qs) < 2:
        return Evidence(
            "C", "Current quarterly earnings", None, thresh, "unknown",
            "insufficient quarterly EPS history", {"quarters": len(qs)},
        )

    latest = qs[0]
    prior = _match_year_ago(qs, latest)
    if prior is None:
        # Fall back to the second-most-recent quarter only if it is a year-ago
        # match; otherwise report unknown rather than comparing mismatched
        # periods, which is how spurious growth numbers get invented.
        return Evidence(
            "C", "Current quarterly earnings", None, thresh, "unknown",
            "no year-ago quarter available to compare (Indian series gaps)",
            {"latest_period": latest.period_end, "latest_eps": latest.eps,
             "quarters": len(qs)},
        )

    growth = _yoy_growth(latest.eps, prior)
    if growth is None:
        return Evidence(
            "C", "Current quarterly earnings", None, thresh, "unknown",
            "year-ago EPS was zero; growth undefined",
            {"latest_period": latest.period_end, "prior_eps": prior},
        )

    accel: float | None = None
    prev_q = qs[1]
    prev_prior = _match_year_ago(qs, prev_q)
    if prev_prior:
        prev_growth = _yoy_growth(prev_q.eps, prev_prior)
        if prev_growth is not None:
            accel = growth - prev_growth

    passed = growth >= thresh and (accel is None or accel >= accel_thresh)
    if accel is None:
        rationale = f"EPS +{growth:.0f}% YoY (>= {thresh:.0f}%); acceleration unverified"
    elif passed:
        rationale = f"EPS +{growth:.0f}% YoY and accelerating (+{accel:.0f}pts)"
    else:
        why = []
        if growth < thresh:
            why.append(f"growth {growth:.0f}% < {thresh:.0f}%")
        if accel is not None and accel < accel_thresh:
            why.append(f"acceleration {accel:+.0f}pts < {accel_thresh:.0f}pts")
        rationale = "; ".join(why)

    return Evidence(
        "C", "Current quarterly earnings", round(growth, 2), thresh,
        "pass" if passed else "fail", rationale,
        {
            "latest_period": latest.period_end,
            "latest_eps": latest.eps,
            "year_ago_eps": prior,
            "growth_pct": round(growth, 2),
            "acceleration_pts": None if accel is None else round(accel, 2),
            "quarters_available": len(qs),
        },
    )


# --------------------------------------------------------------------------
# A - annual earnings
# --------------------------------------------------------------------------


def score_a(ctx: ScoreContext) -> Evidence:
    thresh = ctx.opt("a_min_growth_pct", 20.0)
    years_req = ctx.opt("a_min_years", 3)
    ann = ctx.fundamentals.annual_eps
    if len(ann) < 2:
        return Evidence(
            "A", "Annual earnings growth", None, thresh, "unknown",
            "insufficient annual EPS history", {"years": len(ann)},
        )

    latest, prior = ann[0], ann[1]
    growth = _yoy_growth(latest.eps, prior.eps)

    # O'Neil wants sustained multi-year growth, not one good year.
    streaks = 0
    series_growths: list[float] = []
    for i in range(len(ann) - 1):
        g = _yoy_growth(ann[i].eps, ann[i + 1].eps)
        if g is None:
            continue
        series_growths.append(g)
        if g >= thresh:
            streaks += 1
        else:
            break

    cagr = None
    if len(ann) >= years_req + 1 and ann[-1].eps > 0 and latest.eps > 0:
        n = len(ann) - 1
        cagr = ((latest.eps / ann[-1].eps) ** (1 / n) - 1) * 100.0

    passed = (
        growth is not None
        and growth >= thresh
        and (streaks >= years_req - 1 or (cagr is not None and cagr >= thresh))
    )
    bits = []
    if growth is not None:
        bits.append(f"latest year +{growth:.0f}%")
    if cagr is not None:
        bits.append(f"{years_req}y CAGR {cagr:.0f}%")
    bits.append(f"{streaks} consecutive years >= {thresh:.0f}%")

    return Evidence(
        "A", "Annual earnings growth",
        None if growth is None else round(growth, 2), thresh,
        "pass" if passed else "fail",
        ("; ".join(bits) if passed else "fails: " + "; ".join(bits)),
        {
            "growth_pct": None if growth is None else round(growth, 2),
            "cagr_pct": None if cagr is None else round(cagr, 2),
            "consecutive_years": streaks,
            "years_available": len(ann),
            "series_growth_pct": [round(g, 2) for g in series_growths],
        },
    )


# --------------------------------------------------------------------------
# N - new high
# --------------------------------------------------------------------------


def score_n(ctx: ScoreContext) -> Evidence:
    """Price-new-high proxy.

    The letter's original sense includes a genuinely new product or service.
    That is not derivable from price and earnings data, so this scores the
    price half only and says so.
    """
    lookback = ctx.opt("n_lookback", 250)
    prox_pct = ctx.opt("n_new_high_proximity_pct", 15.0)
    bars = ctx.series.bars
    if len(bars) < 30:
        return Evidence("N", "New (price new high)", None, prox_pct, "unknown",
                        "insufficient history", {"bars": len(bars)})

    window = bars[-lookback:]
    prior_high = max(b.high for b in window[:-1])
    last = bars[-1]
    near = last.close >= prior_high * (1 - prox_pct / 100.0)
    is_high = last.high >= prior_high

    passed = near
    if is_high:
        rationale = "closed at a new high"
    elif near:
        rationale = f"within {prox_pct:.0f}% of {lookback}-bar high ({prior_high:.2f})"
    else:
        rationale = f"{(prior_high - last.close) / last.close * 100:.1f}% below prior high"

    return Evidence(
        "N", "New (price new high)",
        round((last.close / prior_high - 1) * 100, 2), prox_pct,
        "pass" if passed else "fail", rationale,
        {
            "proxy_only": True,
            "note": "scored on price new-high, not product/service news",
            "prior_high": round(prior_high, 4),
            "last_close": round(last.close, 4),
            "distance_pct": round((prior_high - last.close) / last.close * 100, 2),
            "at_new_high": is_high,
        },
    )


# --------------------------------------------------------------------------
# S - supply and demand
# --------------------------------------------------------------------------


def score_s(ctx: ScoreContext) -> Evidence:
    dryup = ctx.opt("s_max_dryup_ratio", 0.80)
    breakout_mult = ctx.opt("s_breakout_volume_mult", 1.4)
    bars = ctx.series.bars
    if len(bars) < 40:
        return Evidence("S", "Supply and demand", None, dryup, "unknown",
                        "insufficient history", {"bars": len(bars)})

    vol = [b.volume for b in bars]
    recent = sum(vol[-5:]) / 5
    base = sum(vol[-60:-5]) / max(1, len(vol[-60:-5]))
    ratio = (recent / base) if base else None

    # Float: small float means supply is easy to move.
    float_shares = ctx.fundamentals.float_shares
    shares_out = ctx.fundamentals.shares_outstanding
    float_pct = None
    if float_shares and shares_out and shares_out > 0:
        float_pct = float_shares / shares_out * 100.0

    if ratio is None:
        return Evidence("S", "Supply and demand", None, dryup, "unknown",
                        "volume baseline unavailable", {"bars": len(bars)})

    passed = ratio <= dryup
    detail: dict[str, Any] = {
        "recent_vol_vs_base": round(ratio, 3),
        "float_pct": None if float_pct is None else round(float_pct, 2),
        "recent_avg_vol": round(recent, 2),
        "base_avg_vol": round(base, 2),
    }
    if float_pct is None:
        detail["float_note"] = "float shares unavailable"

    if passed:
        rationale = f"volume contracted to {ratio:.2f}x of 60d average"
    else:
        rationale = f"volume at {ratio:.2f}x of 60d average (want <= {dryup:.2f}x)"

    return Evidence(
        "S", "Supply and demand", round(ratio, 3), dryup,
        "pass" if passed else "fail", rationale, detail,
    )


# --------------------------------------------------------------------------
# L - relative strength
# --------------------------------------------------------------------------


def score_l(ctx: ScoreContext) -> Evidence:
    """Relative strength versus the benchmark.

    Computed as the stock's weighted return over the window minus the
    benchmark's, which approximates a percentile-free RS line. We avoid
    pretending to have IBD's proprietary RS Rating.
    """
    min_rs = ctx.opt("l_min_rs", 0.0)
    lookback = ctx.opt("l_lookback", 60)
    bars = ctx.series.bars
    if len(bars) < lookback + 1:
        return Evidence("L", "Leader / relative strength", None, min_rs, "unknown",
                        "insufficient history", {"bars": len(bars)})

    stock_ret = (bars[-1].close / bars[-1 - lookback].close - 1) * 100

    bench_ret = None
    bench_sym = None
    if ctx.bench_series is not None and len(ctx.bench_series.bars) >= lookback + 1:
        bb = ctx.bench_series.bars
        bench_ret = (bb[-1].close / bb[-1 - lookback].close - 1) * 100
        bench_sym = ctx.bench_series.symbol

    rs = stock_ret if bench_ret is None else stock_ret - bench_ret
    passed = rs >= min_rs

    detail: dict[str, Any] = {
        "stock_return_pct": round(stock_ret, 2),
        "benchmark": bench_sym,
        "benchmark_return_pct": None if bench_ret is None else round(bench_ret, 2),
        "lookback_days": lookback,
        "note": "excess return vs benchmark, not an IBD RS Rating",
    }
    if bench_ret is None:
        rationale = f"+{stock_ret:.1f}% over {lookback}d (no benchmark; absolute only)"
    elif passed:
        rationale = f"+{stock_ret:.1f}% vs benchmark {bench_ret:+.1f}% -> RS {rs:+.1f}"
    else:
        rationale = f"RS {rs:+.1f} lags benchmark (want >= {min_rs:+.0f})"

    return Evidence(
        "L", "Leader / relative strength", round(rs, 2), min_rs,
        "pass" if passed else "fail", rationale, detail,
    )


# --------------------------------------------------------------------------
# M - market direction
# --------------------------------------------------------------------------


def score_m(ctx: ScoreContext) -> Evidence:
    """Market direction from the benchmark's own trend.

    Confirmation is required: a technically perfect setup in a falling market
    is a lower-probability trade, which the grade reflects.
    """
    ma_days = ctx.opt("m_ma_days", 50)
    if ctx.bench_series is None or len(ctx.bench_series.bars) < ma_days + 5:
        return Evidence("M", "Market direction", None, None, "unknown",
                        "benchmark unavailable", {"ma_days": ma_days})

    closes = [b.close for b in ctx.bench_series.bars]
    ma = sum(closes[-ma_days:]) / ma_days
    last = closes[-1]
    above = last > ma

    # 20-day slope as a secondary confirmation.
    ma20 = sum(closes[-20:]) / 20
    rising = ma20 > sum(closes[-40:-20]) / 20

    passed = above and rising
    if passed:
        rationale = f"benchmark {last:.0f} above {ma_days}d MA {ma:.0f}, MA rising"
    elif above:
        rationale = f"above MA but 20d MA not rising"
    else:
        rationale = f"benchmark {last:.0f} below {ma_days}d MA {ma:.0f}"

    return Evidence(
        "M", "Market direction", round((last / ma - 1) * 100, 2), 0.0,
        "pass" if passed else "fail", rationale,
        {
            "benchmark": ctx.bench_series.symbol,
            "last": round(last, 2),
            "ma": round(ma, 2),
            "above_ma": above,
            "ma_rising": rising,
        },
    )


# --------------------------------------------------------------------------
# I - institutional sponsorship
# --------------------------------------------------------------------------


def score_i(ctx: ScoreContext) -> Evidence:
    pct = ctx.fundamentals.institutional_pct
    if pct is None:
        if ctx.market == "IN":
            return Evidence(
                "I", "Institutional sponsorship", None, None, "unknown",
                "unavailable for India: no free institutional holder data "
                "(yfinance has US only). Scored as unknown, not failed.",
                {"data_gap": True, "market": ctx.market},
            )
        return Evidence(
            "I", "Institutional sponsorship", None, None, "unknown",
            "institutional holder data unavailable", {"data_gap": True},
        )

    # Thresholds are loose: we are checking that meaningful sponsorship exists,
    # not reproducing IBD's accumulation ratings.
    good_pct = ctx.opt("i_good_pct", 40.0)
    passed = pct >= good_pct
    return Evidence(
        "I", "Institutional sponsorship", round(pct, 2), good_pct,
        "pass" if passed else "fail",
        f"{pct:.1f}% held institutionally vs {good_pct:.0f}% target",
        {
            "institutional_pct": round(pct, 2),
            "holders": len(ctx.fundamentals.institutional_detail),
        },
    )


LETTERS: dict[str, Any] = {
    "C": score_c, "A": score_a, "N": score_n, "S": score_s,
    "L": score_l, "M": score_m, "I": score_i,
}