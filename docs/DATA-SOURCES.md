# Data sources

Everything here was measured against the live source on 2026-10-20, not read
from documentation. Where a claim is unverified it says so.

---

## yfinance (current price/EPS source)

Registered as `yfinance` in the `datafeed` registry. Methods:
`price_series`, `fundamentals`, `index_for`.

### Works

| Item | Result |
|---|---|
| India quotes | `RELIANCE.NS`, `TCS.NS`, `INFY.NS`, `HDFCBANK.NS`, `ITC.NS`, `SBIN.NS`, `LT.NS`, `ICICIBANK.NS` all return data |
| Quarterly + annual EPS | Available for both markets |
| `floatShares` / `sharesOutstanding` | Available for both markets |

`TATAMOTORS.NS` returns empty. Not a bug: the symbol was delisted/demerged.

### Does not work

| Item | Result |
|---|---|
| `institutional_holders` for `.NS` | **0 rows for every Indian ticker.** 10 rows for `AAPL`. |
| Quarterly EPS completeness | Gaps. `SBIN.NS` has `nan`; `RELIANCE.NS` is missing 2025-09-30 |

**Consequence.** The `I` letter is `unknown` for all of India. YoY matching must
gather by fiscal year + quarter rather than by array position, because the
series has holes. See DATA-5, STRAT-5.

### Throughput

| Mode | Rate |
|---|---|
| Serial | 1.42 s/symbol |
| 8 workers | 0.04 s/symbol, ~2 min for ~2500 symbols |

Benchmarks: `^NSEI` (India), `^GSPC` (US).

---

## NSE MCP (official, `mcp.nseindia.in`)

Launched by NSE around 2026-10-03. Two Streamable-HTTP servers, **no
authentication**.

| Server | Endpoint | Gives |
|---|---|---|
| `nse-bhavcopy-redis-mcp` | `https://mcp.nseindia.in/bhavcopy/cm/mcp` | EOD OHLCV, ~5000 NSE equities |
| `cm-market` | `https://mcp.nseindia.in/cmmkt/mcp` | live snapshot |

TLS certificate is genuinely issued to `O=National Stock Exchange of India Ltd.`

### Protocol notes

- `initialize` returns `protocolVersion: 2025-06-18`. Response may be
  `application/json` **or** `text/event-stream`; a client must handle both.
- Responses arrive as SSE framing: `event:message` then `data:{...}`.
- `Mcp-Session-Id` is returned on `initialize` and must be sent as a header
  afterwards. `notifications/initialized` returns `202`.
- Some responses are gzip-encoded even without an explicit request. Use
  `curl --compressed` or an equivalent.
- Session reuse works. Fresh parallel sessions work.

### bhavcopy server: 13 tools

| Tool | Params |
|---|---|
| `get_stock_history` | `symbol`, `months` (**max 3**), `endDate` |
| `get_ltp_by_date` | `symbol`, `date` |
| `get_bulk_quote` | `symbols` (**max 50**) |
| `get_52_week_high_low` | `symbol` |
| `get_volume_analysis` | `symbol`, `days` |
| `moving_average` | `symbol`, `days` |
| `get_top_by_volume` | `date`, `n`, `sortBy` |
| `get_top_movers` | `date`, `n`, `direction` |
| `get_market_breadth` | `date` |
| `get_corporate_actions` | `symbol`, `fromDate`, `toDate` |
| `compare_stocks` | `symbols`, `months` |
| `nse_lookup_symbol` | `query` |
| `search_symbols` | `query` |

`resources/list` returns empty. No bulk history resource.

### Measured behaviour

| Metric | Result |
|---|---|
| Latency | 0.09-0.17 s/call |
| Parallelism | 8 concurrent fresh sessions, all clean |
| 5-year page for `RELIANCE` | 11 calls, 2.0 s, 681 rows |
| `get_stock_history` window | ~60-65 trading days per call regardless of `months` (capped at 3) |
| Row fields | `open high low close volume prevClose totalTradedValue`, plus `deliveryQty` |

### Problems found

| Problem | Detail |
|---|---|
| **2023 is entirely missing** | Every 2023 date probed returns `n=0`, across `RELIANCE`/`TCS`/`INFY`/`ITC`/`SBIN`, with retries. `get_market_breadth(2023-06-15)` silently falls back to `2023-01-10`. |
| `deliveryQty` always 0 | Every row. Not usable. |
| No fundamentals | OHLCV, volume, 52w, SMA, corporate actions, breadth only. No EPS, no float, no shareholding. |
| Unreachable by `next_end_date` | The auto-page chain stops around 2024-01. Explicit `endDate` reaches 2021-08-13, so coverage is ~5 years minus the 2023 hole. |
| `get_ltp_by_date` suspicious | `2023-06-15` and `2023-11-15` both return `2558.4`. Treat as unreliable inside the hole. |
| Transient empties | Some month probes returned 0 rows then succeeded on retry. Always retry before concluding "empty". |
| `cm-market` timed out | 45 s, 0 bytes, outside market hours. Unverified whether it works in session. |

### Terms

Responses carry: "for informational and educational purposes only and does not
constitute investment advice or recommendation." Also a SEBI disclaimer. Record
this position before relying on it (INFRA-7).

---

## Universe sources

| Market | Source | Problem |
|---|---|---|
| India | `https://archives.nsiindia.com/content/indices/ind_nifty50list.csv` | **Host does not resolve** (`nsiindia` vs `nseindia`), and it is a 50-name index file either way. `load_nse` therefore falls back to the 48-symbol `FALLBACK_NSE`. See DATA-1, DATA-2. |
| US | `https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv` | 503 rows, works. |

`FALLBACK_NSE` (48 liquid names) and `FALLBACK_SP500` (50) exist in
`universe.py` so a scan never hard-fails on network.

---

## Not yet acquired

| Data | Needed for | Item |
|---|---|---|
| India FII/DII shareholding | `I` on NSE names | DATA-5 |
| Point-in-time EPS | Removing C/A look-ahead in the backtest | DATA-6 |
| Split/bonus-adjusted prices | Correct cup geometry, correct YoY | DATA-7 |

---

## Rejected sources

| Source | Why |
|---|---|
| `@weaxs/openclaw-stock-analysis` plugin | Market set A/HK/US/JP/KR/TW, Chinese copy, no cup-with-handle, no India (DEC-1) |
| Community NSE MCP servers | Built on `nseindia.com` public endpoints that return `403` to non-browser clients; reaching them means forging browser headers. The official server removes the need. |
