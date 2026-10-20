"""Market session status (US / India).

Computed from the clock and each exchange's regular hours -- no network call,
because "is the market open" should not depend on a data provider being up.
Weekends are treated as closed. Exchange holidays are NOT modelled yet, so a
holiday will read as open; that gap is recorded in the returned `note` rather
than hidden, because a status panel that is silently wrong is worse than one
that admits its limits.

Hours (regular session, local exchange time):
  US   NYSE/Nasdaq  09:30 - 16:00 America/New_York, Mon-Fri
  IN   NSE/BSE      09:15 - 15:30 Asia/Kolkata, Mon-Fri
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

SESSIONS = {
    "US": {
        "label": "United States (NYSE/Nasdaq)",
        "tz": "America/New_York",
        "open": (9, 30),
        "close": (16, 0),
        "pre": (4, 0),
        "post": (20, 0),
    },
    "IN": {
        "label": "India (NSE/BSE)",
        "tz": "Asia/Kolkata",
        "open": (9, 15),
        "close": (15, 30),
        "pre": (9, 0),
        "post": (15, 40),
    },
}


def _minutes(t: tuple[int, int]) -> int:
    return t[0] * 60 + t[1]


def session_status(market: str, *, now: datetime | None = None) -> dict:
    """Status for one market: open | closed | pre | post, plus a note."""
    cfg = SESSIONS.get(market.upper())
    if not cfg:
        return {"market": market, "status": "unknown",
                "note": "no session rules for this market"}

    tz = ZoneInfo(cfg["tz"])
    local = (now or datetime.now(ZoneInfo("UTC"))).astimezone(tz)
    mins = local.hour * 60 + local.minute
    weekday = local.weekday()  # 0=Mon .. 6=Sun

    if weekday >= 5:
        status = "closed"
        note = "weekend"
    elif _minutes(cfg["pre"]) <= mins < _minutes(cfg["open"]):
        status, note = "pre", "pre-market"
    elif _minutes(cfg["open"]) <= mins < _minutes(cfg["close"]):
        status, note = "open", "regular session"
    elif _minutes(cfg["close"]) <= mins < _minutes(cfg["post"]):
        status, note = "post", "after hours"
    else:
        status, note = "closed", "outside session"

    return {
        "market": market.upper(),
        "label": cfg["label"],
        "status": status,
        "is_open": status == "open",
        "local_time": local.strftime("%Y-%m-%d %H:%M"),
        "tz": cfg["tz"],
        "note": note + " (holidays not modelled)",
    }


def all_sessions(*, now: datetime | None = None) -> dict:
    return {m: session_status(m, now=now) for m in SESSIONS}


__all__ = ["session_status", "all_sessions", "SESSIONS"]
