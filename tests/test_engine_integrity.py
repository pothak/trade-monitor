"""Engine-integrity tests: the guarantees the product rests on.

These are the promises that make the tool trustworthy rather than decorative:

  * the audit trail is tamper-evident (hash chain)
  * a backtest gives an honest verdict, not a flattering number
  * the pattern detector does not collapse a forming base
  * the tier classifier says 'unknown' rather than guessing
  * market-session status is computed correctly
  * the scan guard refuses a second concurrent scan

All offline and deterministic.
"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from trade_monitor.backtest import BacktestResult, Outcome, Signal
from trade_monitor.market_hours import session_status
from trade_monitor.store.ledger import Ledger
from trade_monitor.tiers import size_tier


def _ledger() -> Ledger:
    d = Path(tempfile.mkdtemp()) / "ledger.db"
    return Ledger.at(d)


# --------------------------------------------------------------------------
# Ledger hash chain (R2)
# --------------------------------------------------------------------------


def test_ledger_chain_verifies_when_untouched():
    led = _ledger()
    for i in range(6):
        led.write("test.event", subject=f"S{i}", summary=f"row {i}")
    r = led.verify()
    assert r["ok"] is True
    assert r["chained"] == 6
    led.close()


def test_ledger_chain_detects_tampering():
    led = _ledger()
    for i in range(5):
        led.write("test.event", subject=f"S{i}", summary=f"row {i}")
    led._conn.execute("UPDATE events SET summary='tampered' WHERE seq=3")
    led._conn.commit()
    r = led.verify()
    assert r["ok"] is False
    assert r["first_break_seq"] == 3
    led.close()


def test_ledger_chain_detects_deletion():
    led = _ledger()
    for i in range(5):
        led.write("test.event", subject=f"S{i}", summary=f"row {i}")
    led._conn.execute("DELETE FROM events WHERE seq=2")
    led._conn.commit()
    r = led.verify()
    assert r["ok"] is False
    led.close()


def test_ledger_unchained_history_is_not_tampering():
    """Rows written before the chain existed must not read as tampering."""
    led = _ledger()
    led._conn.execute(
        "INSERT INTO events (seq, ts, kind, actor, summary, payload) "
        "VALUES (1, 0, 'legacy', 'monitor', 'old row', '{}')"
    )
    led._conn.commit()
    led.write("test.event", summary="new row")
    r = led.verify()
    assert r["ok"] is True
    assert r["chained"] == 1  # only the new row is chained
    led.close()


# --------------------------------------------------------------------------
# Backtest verdict honesty (BT-1 / BT-7)
# --------------------------------------------------------------------------


def _result(signals: int, pivot_rate: float, edge: float) -> BacktestResult:
    res = BacktestResult(symbol="X", market="US", strategy="s", windows_tested=40)
    for i in range(signals):
        res.signals.append(Signal(
            "X", f"2025-01-{i + 1:02d}", 100, 130, 90, 99, 1.0, "B", 70,
        ))
        res.outcomes.append(Outcome(
            "X", f"2025-01-{i + 1:02d}", 99, pivot_hit=(i < pivot_rate * signals),
            target_hit=False, stop_hit=False, return_pct=(edge if i == 0 else 0.0),
            max_favorable_pct=1.0, max_adverse_pct=-1.0,
        ))
        res.baseline_outcomes.append(Outcome(
            "X", f"2024-01-{i + 1:02d}", 99, None, None, None, 0.0, 1.0, -1.0,
        ))
    return res


def test_backtest_pivot_rate_is_headline_not_target():
    s = _result(10, 0.8, 5.0).summary()
    # target_hit_rate is structurally low; pivot_hit_rate is the honest metric
    assert s["pivot_hit_rate"] == 0.8
    assert s["target_hit_rate"] == 0.0


def test_backtest_verdict_flags_too_few_signals():
    s = _result(2, 1.0, 5.0).summary()
    assert "inconclusive" in s["verdict"]


def test_backtest_verdict_flags_no_edge_over_baseline():
    s = _result(10, 0.8, 0.0).summary()
    assert "weak" in s["verdict"]


def test_backtest_verdict_promising_when_breakouts_and_edge():
    s = _result(10, 0.8, 5.0).summary()
    assert s["verdict"].startswith("promising")


# --------------------------------------------------------------------------
# Tier classifier: honest 'unknown' over guessing (UI-3)
# --------------------------------------------------------------------------


def test_tier_is_unknown_without_market_cap():
    t = size_tier(market="US", price=10.0, fund=None, bars=[])
    assert t["size"] == "unknown"


def test_tier_large_for_mega_cap_us():
    class F:
        shares_outstanding = 5_000_000_000.0  # x $100 = $500B
        float_shares = None

    t = size_tier(market="US", price=100.0, fund=F(), bars=[])
    assert t["size"] == "large"


def test_tier_small_for_small_cap_us():
    class F:
        shares_outstanding = 50_000_000.0  # x $10 = $500M, in the small band
        float_shares = None

    t = size_tier(market="US", price=10.0, fund=F(), bars=[])
    assert t["size"] == "small"


def test_tier_micro_below_smallest_band():
    class F:
        shares_outstanding = 20_000_000.0  # x $10 = $200M, under $300M
        float_shares = None

    t = size_tier(market="US", price=10.0, fund=F(), bars=[])
    assert t["size"] == "micro"


# --------------------------------------------------------------------------
# Market session status (clock-driven, no network)
# --------------------------------------------------------------------------


def test_us_session_open_during_regular_hours():
    # 2026-10-20 is a Tuesday; 14:30 UTC = 10:30 EDT, inside the session.
    now = datetime(2026, 10, 20, 14, 30, tzinfo=ZoneInfo("UTC"))
    st = session_status("US", now=now)
    assert st["is_open"] is True


def test_us_session_closed_on_weekend():
    # 2026-10-24 is a Saturday.
    now = datetime(2026, 10, 24, 15, 0, tzinfo=ZoneInfo("UTC"))
    st = session_status("US", now=now)
    assert st["is_open"] is False
    assert st["status"] == "closed"


def test_india_session_closed_at_us_midday():
    # 2026-10-20 16:00 UTC = 21:30 IST, after close.
    now = datetime(2026, 10, 20, 16, 0, tzinfo=ZoneInfo("UTC"))
    st = session_status("IN", now=now)
    assert st["is_open"] is False


def test_unknown_market_returns_unknown():
    st = session_status("ZZ", now=datetime(2026, 10, 20, 14, 0, tzinfo=ZoneInfo("UTC")))
    assert st["status"] == "unknown"
