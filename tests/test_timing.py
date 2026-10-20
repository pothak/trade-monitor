"""The timing harness: a scan must report where its time went.

This is not a performance *assertion* (we do not fail on slow machines); it is
a correctness check that the instrumentation itself works and keeps emitting
the breakdown the CLI, the server and docs/PERFORMANCE.md depend on.
"""

from __future__ import annotations

import time

from trade_monitor.timing import Stages


def test_empty_stages_summary():
    s = Stages()
    s.add("fetch", 1.0, count=10)
    summary = s.summary()
    assert summary["total_s"] == 1.0
    assert summary["stages"][0]["stage"] == "fetch"
    assert summary["stages"][0]["count"] == 10
    assert summary["stages"][0]["share_pct"] == 100.0


def test_time_context_manager_accumulates():
    s = Stages()
    for _ in range(3):
        with s.time("detect"):
            time.sleep(0.001)
    assert s.counts["detect"] == 3
    assert s.seconds["detect"] >= 0.003


def test_network_and_compute_classification():
    s = Stages()
    s.add("fetch", 1.0)
    s.add("detect", 0.001)
    s.add("score", 0.001)
    summary = s.summary()
    assert summary["network_share_pct"] > 99
    assert summary["compute_share_pct"] < 1


def test_stages_sorted_by_cost():
    s = Stages()
    s.add("score", 0.01)
    s.add("fetch", 1.0)
    s.add("detect", 0.1)
    rows = s.summary()["stages"]
    assert [r["stage"] for r in rows] == ["fetch", "detect", "score"]


def test_report_is_human_readable():
    s = Stages()
    s.add("fetch", 2.0)
    s.add("detect", 0.001)
    text = s.report()
    assert "fetch" in text
    assert "network" in text
    assert "total" in text
