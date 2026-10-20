"""P1 -- no look-ahead, ever.

The most important guarantee in the project. These tests do not check that the
code *tries* to avoid look-ahead; they check that it is **impossible by
construction**.

If any of these go red, every backtest number becomes meaningless and the
project's core promise is void.
"""

from __future__ import annotations

import pytest

from trade_monitor.store import MarketStore, PointInTimeError


@pytest.fixture
def store(tmp_path) -> MarketStore:
    ms = MarketStore(tmp_path / "market.db")
    from tests.conftest import make_bars

    bars = make_bars(n=50, start_date=__import__("datetime").date(2024, 1, 1))
    ms.put_bars("TEST.NS", bars, source="test")
    yield ms
    ms.close()


# --------------------------------------------------------------------------
# as_of is mandatory
# --------------------------------------------------------------------------


def test_query_requires_as_of(store):
    with pytest.raises(PointInTimeError):
        store.query("")


def test_query_rejects_non_iso_as_of(store):
    for bad in ["latest", "2024-6-3", "2024/06/03", "yesterday"]:
        with pytest.raises(PointInTimeError):
            store.query(bad)


def test_query_accepts_valid_iso_date(store):
    q = store.query("2024-01-15")
    assert q.as_of == "2024-01-15"


# --------------------------------------------------------------------------
# reads never return data past as_of
# --------------------------------------------------------------------------


def test_bars_never_exceed_as_of(store):
    as_of = "2024-01-20"
    bars = store.query(as_of).bars("TEST.NS", lookback=1000)
    assert bars, "should have some bars"
    assert all(b.date <= as_of for b in bars), "look-ahead: a bar past as_of leaked"


def test_bars_grow_as_as_of_moves(store):
    early = store.query("2024-01-10").bars("TEST.NS", lookback=1000)
    later = store.query("2024-02-10").bars("TEST.NS", lookback=1000)
    assert len(early) < len(later)
    assert all(b.date <= "2024-01-10" for b in early)


def test_bars_range_is_clamped_to_as_of(store):
    # end is asked for far beyond as_of; must be clamped
    bars = store.query("2024-01-15").bars_range("TEST.NS", "2024-01-01", "2099-01-01")
    assert all(b.date <= "2024-01-15" for b in bars)


def test_eps_never_exceed_as_of(store):

    from trade_monitor_contracts import QuarterEPS

    eps = [
        QuarterEPS(period_end="2024-01-05", eps=1.0),
        QuarterEPS(period_end="2024-01-25", eps=2.0),
    ]
    store.put_eps("TEST.NS", eps, period="quarter", source="test")

    visible = store.query("2024-01-15").eps("TEST.NS")
    dates = {e["period_end"] for e in visible}
    assert "2024-01-25" not in dates, "look-ahead: future EPS leaked"
    assert "2024-01-05" in dates


def test_series_never_exceed_as_of(store):
    for d in ["2024-01-01", "2024-01-02", "2024-01-03"]:
        store.put_dataset("breadth", "NIFTY", d, 1.0, source="test")
    rows = store.query("2024-01-02").series("breadth", "NIFTY")
    assert all(r["date"] <= "2024-01-02" for r in rows)


# --------------------------------------------------------------------------
# raw() is bounded too -- strategies write their own SQL
# --------------------------------------------------------------------------


def test_raw_requires_as_of_reference(store):
    with pytest.raises(PointInTimeError):
        store.query("2024-01-15").raw("SELECT date FROM bars WHERE symbol=:symbol",
                                      symbol="TEST.NS")


def test_raw_with_as_of_works(store):
    rows = store.query("2024-01-15").raw(
        "SELECT date FROM bars WHERE symbol=:symbol AND date<=:as_of",
        symbol="TEST.NS",
    )
    assert rows
    assert all(r["date"] <= "2024-01-15" for r in rows)


def test_raw_rejects_writes(store):
    q = store.query("2024-01-15")
    for bad in [
        "DELETE FROM bars WHERE date<=:as_of",
        "UPDATE bars SET close=0 WHERE date<=:as_of",
        "DROP TABLE bars",
        "INSERT INTO bars VALUES (1) WHERE date<=:as_of",
        "PRAGMA table_info(bars)",
    ]:
        with pytest.raises(PointInTimeError):
            q.raw(bad)


def test_raw_cannot_override_as_of(store):
    # Passing as_of as a param must not let the caller escape the boundary.
    rows = store.query("2024-01-15").raw(
        "SELECT date FROM bars WHERE symbol=:symbol AND date<=:as_of",
        symbol="TEST.NS", as_of="2099-01-01",
    )
    assert all(r["date"] <= "2024-01-15" for r in rows), "caller overrode as_of"


# --------------------------------------------------------------------------
# the store itself is idempotent (re-fetch does not duplicate)
# --------------------------------------------------------------------------


def test_put_bars_is_idempotent(store):
    from tests.conftest import make_bars

    before = store.coverage()["rows"]
    bars = make_bars(n=50, start_date=__import__("datetime").date(2024, 1, 1))
    store.put_bars("TEST.NS", bars, source="test")
    after = store.coverage()["rows"]
    assert after == before, "re-fetching duplicated rows"
