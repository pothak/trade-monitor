"""The plugin console: declarative feeds, discovery, manifests, CRUD.

Everything here is offline. The one thing that touches the network -- the
generic driver actually fetching -- is tested with a local HTTP server, so it
exercises the real request/mapping path without depending on Yahoo.
"""

from __future__ import annotations

import http.server
import json
import socketserver
import threading

import pytest

from trade_monitor.datafeed.spec_feed import (
    FeedSpecError,
    SpecFeed,
    json_path,
    parse_spec,
)

# --------------------------------------------------------------------------
# Spec parsing
# --------------------------------------------------------------------------

VALID_ROWS = '''
[feed]
name = "demo"
kind = "rest"
description = "demo feed"

[feed.http]
url = "https://example.com/{symbol}"

[feed.map]
bars = "$.candles"
date = "t"
open = "o"
high = "h"
low = "l"
close = "c"
volume = "v"

[feed.markets]
IN = "^NSEI"
'''

VALID_COLUMNS = '''
[feed]
name = "cols"

[feed.http]
url = "https://example.com/{symbol}"

[feed.map]
shape = "columns"
date = "$.ts"
open = "$.o"
high = "$.h"
low = "$.l"
close = "$.c"
date_format = "epoch"
'''


def test_parse_rows_spec():
    spec = parse_spec(VALID_ROWS)
    assert spec.name == "demo"
    assert spec.field_map["bars"] == "$.candles"
    assert spec.markets["IN"] == "^NSEI"


def test_parse_columns_spec_needs_no_bars_key():
    spec = parse_spec(VALID_COLUMNS)
    spec.validate()  # must not require `bars`
    assert spec.field_map["shape"] == "columns"


def test_missing_url_rejected():
    with pytest.raises(FeedSpecError):
        parse_spec("[feed]\nname=\"x\"\n").validate()


def test_missing_required_map_keys_rejected():
    bad = VALID_ROWS.replace('close = "c"\n', "")
    with pytest.raises(FeedSpecError):
        parse_spec(bad).validate()


def test_bad_date_format_rejected():
    bad = VALID_ROWS.replace('volume = "v"', 'volume = "v"\ndate_format = "weird"')
    with pytest.raises(FeedSpecError):
        parse_spec(bad).validate()


def test_invalid_toml_rejected():
    with pytest.raises(FeedSpecError):
        parse_spec("this is not toml = = =")


# --------------------------------------------------------------------------
# JSON path
# --------------------------------------------------------------------------


def test_json_path_basics():
    obj = {"a": {"b": [{"c": 1}, {"c": 2}]}}
    assert json_path(obj, "$.a.b.1.c") == 2
    assert json_path(obj, "a.b.0.c") == 1


def test_json_path_missing_key_raises():
    with pytest.raises(FeedSpecError):
        json_path({}, "$.nope")


# --------------------------------------------------------------------------
# The generic driver, against a local HTTP server (real request path)
# --------------------------------------------------------------------------


class _Handler(http.server.BaseHTTPRequestHandler):
    payload: dict = {}
    status = 200

    def do_GET(self):
        body = json.dumps(self.payload).encode()
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def local_server():
    srv = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield port
    srv.shutdown()


def test_driver_rows_against_local_server(local_server):
    _Handler.payload = {
        "candles": [
            {"t": "2024-01-01", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 100},
            {"t": "2024-01-02", "o": 1.5, "h": 3, "l": 1.4, "c": 2.5, "v": 200},
        ]
    }
    spec = parse_spec(
        f'[feed]\nname="r"\n[feed.http]\nurl="http://127.0.0.1:{local_server}/{{symbol}}"\n'
        f'[feed.map]\nbars="$.candles"\ndate="t"\nopen="o"\nhigh="h"\nlow="l"\nclose="c"\nvolume="v"\n'
    )
    feed = SpecFeed(spec)
    series = feed.price_series("X", days=10)
    assert len(series.bars) == 2
    assert series.bars[0].close == 1.5
    assert series.bars[1].date == "2024-01-02"


def test_driver_columns_against_local_server(local_server):
    _Handler.payload = {
        "ts": [1704067200, 1704153600],
        "o": [1, 2], "h": [2, 3], "l": [0.5, 1.5], "c": [1.5, 2.5],
        "v": [100, 200],
    }
    spec = parse_spec(
        f'[feed]\nname="c"\n[feed.http]\nurl="http://127.0.0.1:{local_server}/{{symbol}}"\n'
        f'[feed.map]\nshape="columns"\ndate="$.ts"\nopen="$.o"\nhigh="$.h"\n'
        f'low="$.l"\nclose="$.c"\nvolume="$.v"\ndate_format="epoch"\n'
    )
    series = SpecFeed(spec).price_series("X", days=10)
    assert len(series.bars) == 2
    assert series.bars[0].date == "2024-01-01"


def test_driver_drops_null_rows_columns(local_server):
    _Handler.payload = {
        "ts": [1704067200, 1704153600, 1704240000],
        "o": [1, 2, 3], "h": [2, 3, 4], "l": [0.5, 1.5, 2.5],
        "c": [1.5, None, 3.5],   # middle row has a null close
        "v": [100, 200, 300],
    }
    spec = parse_spec(
        f'[feed]\nname="c"\n[feed.http]\nurl="http://127.0.0.1:{local_server}/{{symbol}}"\n'
        f'[feed.map]\nshape="columns"\ndate="$.ts"\nopen="$.o"\nhigh="$.h"\n'
        f'low="$.l"\nclose="$.c"\ndate_format="epoch"\n'
    )
    series = SpecFeed(spec).price_series("X", days=10)
    assert len(series.bars) == 2, "null-close row should be dropped, not fabricated"


def test_driver_reports_missing_auth_env(local_server):
    spec = parse_spec(
        f'[feed]\nname="a"\n[feed.http]\nurl="http://127.0.0.1:{local_server}/{{symbol}}"\n'
        f'auth_header="Authorization"\nauth_env="DEFINITELY_NOT_SET_XYZ"\n'
        f'[feed.map]\nbars="$.candles"\ndate="t"\nopen="o"\nhigh="h"\nlow="l"\nclose="c"\n'
    )
    with pytest.raises(FeedSpecError):
        SpecFeed(spec).price_series("X", days=10)


def test_spec_feed_reports_degraded_fundamentals():
    spec = parse_spec(VALID_ROWS)
    fund = SpecFeed(spec).fundamentals("X")
    assert fund.degraded, "a price-only feed must declare its gap, not invent data"
