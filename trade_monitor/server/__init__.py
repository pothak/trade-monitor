"""Dashboard server.

A small JSON API over the stores, plus a static frontend. The API is the
contract the frontend talks to, so the frontend can be anything -- the bundled
no-build page, or a separate React app pointed at the same endpoints.

    GET  /api/health          liveness + versions + coverage
    GET  /api/strategies      loaded strategies and their needs
    GET  /api/theses          stored theses (filters: market, min_grade, limit)
    GET  /api/thesis/<id>     one thesis with its ledger timeline
    GET  /api/runs            recent scans
    GET  /api/ledger          tail of the audit trail
    GET  /api/coverage        what data the market store holds
    POST /api/scan            run a scan (symbols or universe)

Server is stdlib only (http.server + json). No framework, no build step, so
`pip install trade-monitor && trade-monitor serve` works with nothing else.
"""

from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import __version__

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_ENGINE_DB = Path("~/.trade-monitor/engine.db").expanduser()
DEFAULT_MARKET_DB = Path("~/.trade-monitor/market.db").expanduser()
DEFAULT_LEDGER_DB = Path("~/.trade-monitor/ledger.db").expanduser()


def _fundamentals_brief(fund) -> dict | None:
    """A compact, JSON-safe view of fundamentals for the data viewer."""
    if fund is None:
        return None
    return {
        "shares_outstanding": getattr(fund, "shares_outstanding", None),
        "float_shares": getattr(fund, "float_shares", None),
        "institutional_pct": getattr(fund, "institutional_pct", None),
        "quarterly_eps": [
            {"period_end": q.period_end, "eps": q.eps}
            for q in (getattr(fund, "quarterly_eps", None) or [])
        ],
        "annual_eps": [
            {"period_end": q.period_end, "eps": q.eps}
            for q in (getattr(fund, "annual_eps", None) or [])
        ],
        "degraded": list(getattr(fund, "degraded", None) or []),
    }


class App:
    """Holds the stores. One instance shared across request threads.

    Stores are opened lazily so `serve` starts instantly even before any scan
    has created the databases.
    """

    def __init__(self, *, engine_db=None, market_db=None, ledger_db=None) -> None:
        self.engine_db = Path(engine_db or DEFAULT_ENGINE_DB).expanduser()
        self.market_db = Path(market_db or DEFAULT_MARKET_DB).expanduser()
        self.ledger_db = Path(ledger_db or DEFAULT_LEDGER_DB).expanduser()
        self._lock = threading.Lock()
        self._engine = None
        self._market = None
        self._ledger = None
        self.job: dict = {"running": False, "market": None, "total": 0,
                          "done": 0, "hits": 0, "errors": 0, "started": None}
        self._uni_cache: dict[str, tuple[float, list[str]]] = {}
        self._tickers_cache: dict[str, tuple[float, dict]] = {}
        self._ai_cache: dict[str, tuple[float, dict]] = {}

    def engine(self):
        from ..store import Store

        if self._engine is None:
            self._engine = Store(self.engine_db)
        return self._engine

    def market(self):
        from ..store import MarketStore

        if self._market is None:
            self._market = MarketStore(self.market_db)
        return self._market

    def ledger(self):
        from ..store import Ledger

        if self._ledger is None:
            self._ledger = Ledger.at(self.ledger_db)
        return self._ledger

    def run_scan(self, *, strategy_names, symbols, market, limit):
        """Run a scan on a background thread so the request returns at once.

        Shares the single-job guard with the universe scan: only one scan (of
        either kind) runs at a time. Without that, two scans write theses for
        the same (strategy, symbol) concurrently and the table shows overlaps.
        """
        import time as _time

        from ..datafeed import REGISTRY as FEEDS
        from ..engine import run
        from ..store import Store

        if self.job.get("running"):
            return {"started": False, "reason": "a scan is already running",
                    "job": self.job}

        self.job = {"running": True, "market": market, "total": len(symbols),
                    "done": 0, "hits": 0, "errors": 0,
                    "started": _time.time(), "kind": "symbols"}

        def _work():
            chunk = 40
            try:
                feed = FEEDS.create("yfinance")
                for i in range(0, len(symbols), chunk):
                    part = symbols[i:i + chunk]
                    store = Store(self.engine_db)
                    try:
                        with self._lock:
                            rep = run(
                                strategy_names=strategy_names,
                                symbols=part,
                                feed=feed,
                                market=market,
                                store=store,
                                market_store=self.market(),
                                ledger=self.ledger(),
                            )
                        hits = sum(r.hits for r in rep.values())
                        errs = sum(r.errors for r in rep.values())
                    except Exception:  # noqa: BLE001
                        hits, errs = 0, len(part)
                    finally:
                        store.close()
                    self.job["done"] += len(part)
                    self.job["hits"] += hits
                    self.job["errors"] += errs
                    self._invalidate_tickers()
            finally:
                self.job["running"] = False
                self._invalidate_tickers()

        t = threading.Thread(target=_work, daemon=True)
        t.start()
        return {"started": True, "symbols": len(symbols), "job": self.job}

    def start_universe_scan(self, *, strategy_names, market, limit=None):
        """Scan the whole market in the background, reporting progress.

        Chunked so the UI can watch it fill in and so a huge universe does not
        hog one giant transaction. One job at a time; a second request while
        running is refused rather than queued.
        """
        import time as _time

        from ..datafeed import REGISTRY as FEEDS
        from ..engine import run
        from ..store import Store
        from ..universe import load_nse, load_sp500

        if self.job.get("running"):
            return {"started": False, "reason": "a scan is already running",
                    "job": self.job}

        uni = load_sp500() if market == "US" else load_nse("IN")
        symbols = list(uni.symbols)
        if limit:
            symbols = symbols[:limit]

        self.job = {"running": True, "market": market, "total": len(symbols),
                    "done": 0, "hits": 0, "errors": 0,
                    "started": _time.time(), "kind": "universe"}

        def _work():
            chunk = 40
            try:
                feed = FEEDS.create("yfinance")
                for i in range(0, len(symbols), chunk):
                    part = symbols[i:i + chunk]
                    store = Store(self.engine_db)
                    try:
                        rep = run(
                            strategy_names=strategy_names,
                            symbols=part,
                            feed=feed,
                            market=market,
                            store=store,
                            market_store=self.market(),
                            ledger=self.ledger(),
                        )
                        hits = sum(r.hits for r in rep.values())
                        errs = sum(r.errors for r in rep.values())
                    except Exception:  # noqa: BLE001 - one chunk must not kill the job
                        hits, errs = 0, len(part)
                    finally:
                        store.close()
                    self.job["done"] += len(part)
                    self.job["hits"] += hits
                    self.job["errors"] += errs
                    self._invalidate_tickers()
            finally:
                self.job["running"] = False
                self._invalidate_tickers()

        t = threading.Thread(target=_work, daemon=True)
        t.start()
        return {"started": True, "symbols": len(symbols), "job": self.job}


    def bars(self, *, symbol, market, days=250):
        """Fetch one symbol's price series + fundamentals for the data viewer.

        Read-only, on the request thread. Uses the same configured feed as a
        scan so what you see is what the strategies see. Never fatal: returns
        an {error} dict instead of raising so the UI can show the reason.
        """
        from ..datafeed import REGISTRY as FEEDS

        try:
            feed = FEEDS.create("yfinance")
            series = feed.price_series(symbol, days=days)
        except Exception as exc:  # noqa: BLE001
            return {"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"}

        bars = [
            {
                "date": b.date, "open": b.open, "high": b.high,
                "low": b.low, "close": b.close, "volume": b.volume,
            }
            for b in series.bars
        ]

        fund = None
        try:
            fund = feed.fundamentals(symbol)
        except Exception:  # noqa: BLE001 - fundamental fetch is best-effort
            fund = None

        from ..tiers import size_tier
        from ..tiers import warnings as tier_warnings

        price = bars[-1]["close"] if bars else None
        tier = size_tier(market=market, price=price, fund=fund, bars=series.bars)
        tier["warnings"] = tier_warnings(tier)

        return {
            "symbol": series.symbol,
            "market": series.market,
            "currency": series.currency,
            "count": len(bars),
            "from": bars[0]["date"] if bars else None,
            "to": bars[-1]["date"] if bars else None,
            "bars": bars,
            "fundamentals": _fundamentals_brief(fund),
            "tier": tier,
        }

    def overview(self):
        """Live macro rows: indices, VIX, yields, dollar, gold, oil, bitcoin."""
        from ..datafeed import REGISTRY as FEEDS
        from ..macro import overview as _ov

        try:
            return _ov(feed=FEEDS.create("yfinance"))
        except Exception as exc:  # noqa: BLE001
            return {"instruments": [], "error": f"{type(exc).__name__}: {exc}"}

    def correlations(self):
        """Daily-return correlation matrix across the macro instruments."""
        from ..datafeed import REGISTRY as FEEDS
        from ..macro import correlations as _corr

        try:
            return _corr(feed=FEEDS.create("yfinance"))
        except Exception as exc:  # noqa: BLE001
            return {"matrix": [], "error": f"{type(exc).__name__}: {exc}"}

    def technicals(self, symbol, market="US", days=260):
        """RSI / MAs / range for one symbol, for the deep-dive view."""
        from ..datafeed import REGISTRY as FEEDS
        from ..macro import technicals as _tech

        try:
            s = FEEDS.create("yfinance").price_series(symbol, days=days)
            return {"symbol": symbol, "technicals": _tech(s.bars)}
        except Exception as exc:  # noqa: BLE001
            return {"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"}

    def _invalidate_tickers(self) -> None:
        self._tickers_cache.clear()

    def tickers(self, market=None, *, whole_universe=True, q=None, status=None,
                min_grade=None, sort="symbol", order="asc", page=1, page_size=100):
        """Screener rows for a market, cached in memory.

        Building the rows reads the universe (a ~400ms static load) and the
        theses table; that is far too slow to repeat per keystroke. So the
        merged rows are built once per market, cached for a short TTL, and every
        filter/sort/page runs over the cached list. A scan clears the cache.
        """
        import time as _time

        key = market or "ALL"
        hit = self._tickers_cache.get(key)
        now = _time.time()
        if hit and (now - hit[0]) < 30:
            base = hit[1]
        else:
            base = self._build_tickers(market=market, whole_universe=whole_universe)
            self._tickers_cache[key] = (now, base)

        return self._filter_tickers(
            base, q=q, status=status, min_grade=min_grade,
            sort=sort, order=order, page=page, page_size=page_size,
        )

    def _build_tickers(self, market=None, *, whole_universe=True):
        """Build the full merged row set for a market (uncached)."""
        theses = self.engine().list_theses(market=market, limit=5000)
        fresh = {i["symbol"]: i for i in self.market().freshness()["items"]}

        rows: dict[str, dict] = {}
        order_map = {"A": 0, "B": 1, "C": 2, "D": 3, "F": 4}

        if whole_universe and market in ("IN", "US"):
            try:
                from ..universe import load_nse, load_sp500

                uni = load_sp500() if market == "US" else load_nse("IN")
                for s in uni.symbols:
                    rows[s] = {
                        "symbol": s, "market": market,
                        "currency": "USD" if market == "US" else "INR",
                        "grades": {}, "strategies": {}, "thesis_ids": {},
                        "best_grade": None, "best_confidence": None,
                        "pattern": None, "degraded": [], "as_of": None,
                        "tier": None, "scanned": False,
                        "price": None, "target": None, "stop": None,
                    }
            except Exception:  # noqa: BLE001 - fall back to scored-only
                pass

        for t in theses:
            sym = t.get("symbol")
            if not sym:
                continue
            row = rows.get(sym)
            if row is None:
                row = rows[sym] = {
                    "symbol": sym, "market": t.get("market"),
                    "currency": t.get("currency"),
                    "grades": {}, "strategies": {}, "thesis_ids": {},
                    "best_grade": None, "best_confidence": None,
                    "pattern": None, "degraded": [], "as_of": None,
                    "tier": None, "scanned": False,
                    "price": None, "target": None, "stop": None,
                }
            row["scanned"] = True
            row["market"] = t.get("market") or row.get("market")
            row["currency"] = t.get("currency") or row.get("currency")
            if t.get("payload", {}).get("tier"):
                row["tier"] = t["payload"]["tier"]
            # Price + target: taken from what the scan recorded, never invented.
            pl = t.get("payload", {}) or {}
            if pl.get("last_price") is not None:
                row["price"] = pl["last_price"]
            elif row.get("price") is None:
                inv = t.get("invalidation") or {}
                if inv.get("stop_price"):
                    row["price"] = round(inv["stop_price"] / 0.92, 4)
            tgt = t.get("measured_move_target")
            if tgt is not None:
                row["target"] = tgt
            inv = t.get("invalidation") or {}
            if inv.get("stop_price") is not None:
                row["stop"] = inv["stop_price"]
            strat = t.get("strategy")
            row["strategies"][strat] = t.get("confidence")
            row["grades"][strat] = t.get("grade")
            row["thesis_ids"][strat] = t.get("id")
            row["as_of"] = t.get("as_of") or row.get("as_of")
            if t.get("pattern"):
                row["pattern"] = t.get("pattern")
            for d in (t.get("degraded_inputs") or []):
                if d not in row["degraded"]:
                    row["degraded"].append(d)
            g = t.get("grade")
            if g and (row["best_grade"] is None or
                      order_map.get(g, 9) < order_map.get(row["best_grade"], 9)):
                row["best_grade"] = g
                row["best_confidence"] = t.get("confidence")

        for sym, row in rows.items():
            fi = fresh.get(sym)
            if fi:
                row["fetched_age_s"] = fi["age_s"]
                row["fetched_rows"] = fi["rows"]

        all_rows = list(rows.values())
        total_universe = len(all_rows)
        scanned_count = sum(1 for r in all_rows if r["scanned"])
        return {
            "rows": all_rows,
            "universe": total_universe,
            "scanned": scanned_count,
        }

    def _filter_tickers(self, base, *, q=None, status=None, min_grade=None,
                        sort="symbol", order="asc", page=1, page_size=100):
        """Apply filter/sort/pagination over the cached row set."""
        order_map = {"A": 0, "B": 1, "C": 2, "D": 3, "F": 4}
        result = base["rows"]
        total_universe = base["universe"]
        scanned_count = base["scanned"]

        # -- filtering ----------------------------------------------------
        if q:
            needle = q.strip().upper()
            result = [r for r in result if needle in r["symbol"].upper()]
        if status == "scanned":
            result = [r for r in result if r["scanned"]]
        elif status == "pending":
            result = [r for r in result if not r["scanned"]]
        if min_grade:
            cap = order_map.get(min_grade.upper(), 4)
            result = [r for r in result
                      if r["best_grade"] and order_map.get(r["best_grade"], 9) <= cap]

        # -- sorting ------------------------------------------------------
        def _size(r):
            return (r.get("tier") or {}).get("size", "")

        def _liq(r):
            return (r.get("tier") or {}).get("liquidity", "")

        keyfuncs = {
            "symbol": lambda r: r["symbol"],
            "market": lambda r: r.get("market") or "",
            "best_grade": lambda r: order_map.get(r["best_grade"], 99),
            "best_confidence": lambda r: (r["best_confidence"] is not None,
                                          r["best_confidence"] or -1),
            "size": _size,
            "liquidity": _liq,
            "as_of": lambda r: r.get("as_of") or "",
            "fetched_age_s": lambda r: (r.get("fetched_age_s") is not None,
                                        r.get("fetched_age_s") or 1e12),
            "scanned": lambda r: r["scanned"],
            "price": lambda r: (r.get("price") is not None, r.get("price") or 0),
            "target": lambda r: (r.get("target") is not None, r.get("target") or 0),
        }
        keyfunc = keyfuncs.get(sort, keyfuncs["symbol"])
        result = sorted(result, key=keyfunc, reverse=(order == "desc"))

        # -- pagination ---------------------------------------------------
        filtered = len(result)
        page = max(1, int(page or 1))
        page_size = max(1, min(int(page_size or 100), 1000))
        start = (page - 1) * page_size
        page_rows = result[start:start + page_size]

        return {
            "tickers": page_rows,
            "count": len(page_rows),
            "scanned": scanned_count,
            "pending": total_universe - scanned_count,
            "universe": total_universe,
            "filtered": filtered,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (filtered + page_size - 1) // page_size),
            "query": {"q": q, "status": status, "min_grade": min_grade,
                      "sort": sort, "order": order},
        }




class Handler(BaseHTTPRequestHandler):
    app: App  # set by serve()

    def log_message(self, fmt, *args):  # quieter
        return

    # -- helpers ---------------------------------------------------------

    def _json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, msg: str, status: int = 400) -> None:
        self._json({"error": msg}, status)

    def _static(self, path: str) -> None:
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (STATIC_DIR / rel).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.exists():
            self._error("not found", 404)
            return
        ctype = {
            ".html": "text/html", ".js": "application/javascript",
            ".css": "text/css", ".json": "application/json", ".svg": "image/svg+xml",
        }.get(target.suffix, "application/octet-stream")
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- routes ----------------------------------------------------------

    def do_GET(self) -> None:
        url = urlparse(self.path)
        params = {k: v[0] for k, v in parse_qs(url.query).items()}
        path = url.path

        if not path.startswith("/api/"):
            self._static(path)
            return

        try:
            if path == "/api/health":
                self._json(self._health())
            elif path == "/api/strategies":
                self._json(self._strategies())
            elif path == "/api/theses":
                self._json(self._theses(params))
            elif path.startswith("/api/thesis/") and path.endswith("/ai"):
                tid = path[len("/api/thesis/"):-len("/ai")]
                self._json(self._thesis_ai(tid))
            elif path.startswith("/api/thesis/"):
                self._json(self._thesis(path.rsplit("/", 1)[-1]))
            elif path == "/api/runs":
                self._json(self.app.engine().recent_runs(int(params.get("limit", 20))))
            elif path == "/api/ledger":
                self._json(self._ledger(params))
            elif path == "/api/ledger/verify":
                self._json(self.app.ledger().verify())
            elif path == "/api/coverage":
                self._json(self.app.market().coverage())
            elif path == "/api/freshness":
                self._json(self.app.market().freshness(
                    kind=params.get("kind", "bars")))
            elif path == "/api/symbols":
                self._json(self._symbols(params))
            elif path == "/api/bars":
                self._json(self._bars(params))
            elif path == "/api/overview":
                self._json(self.app.overview())
            elif path == "/api/correlations":
                self._json(self.app.correlations())
            elif path == "/api/tickers":
                def _int(name, default):
                    try:
                        return int(params.get(name, default))
                    except (TypeError, ValueError):
                        return default

                self._json(self.app.tickers(
                    market=params.get("market"),
                    q=params.get("q"),
                    status=params.get("status"),
                    min_grade=params.get("min_grade"),
                    sort=params.get("sort", "symbol"),
                    order=params.get("order", "asc"),
                    page=_int("page", 1),
                    page_size=_int("page_size", 100),
                ))
            elif path == "/api/job":
                self._json(self.app.job)
            elif path == "/api/sessions":
                from ..market_hours import all_sessions

                self._json({"sessions": all_sessions()})
            elif path == "/api/plugins":
                self._json(self._plugins(params))
            elif path.startswith("/api/plugins/"):
                self._json(self._plugin_one(path))
            else:
                self._error("unknown endpoint", 404)
        except Exception as exc:  # noqa: BLE001 - never crash the server
            self._error(f"{type(exc).__name__}: {exc}", 500)

    def do_POST(self) -> None:
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._error("invalid JSON")
            return

        try:
            if url.path == "/api/scan":
                self._json(self._scan(body))
            elif url.path == "/api/scan-all":
                self._json(self._scan_all(body))
            elif url.path == "/api/plugins":
                self._json(self._plugin_create(body))
            elif url.path == "/api/plugins/validate":
                self._json(self._plugin_validate(body))
            elif url.path.endswith("/toggle") and "/api/plugins/" in url.path:
                self._json(self._plugin_toggle(url.path))
            else:
                self._error("unknown endpoint", 404)
        except Exception as exc:  # noqa: BLE001
            self._error(f"{type(exc).__name__}: {exc}", 500)

    def do_DELETE(self) -> None:
        url = urlparse(self.path)
        try:
            if url.path.startswith("/api/plugins/"):
                self._json(self._plugin_delete(url.path))
            else:
                self._error("unknown endpoint", 404)
        except Exception as exc:  # noqa: BLE001
            self._error(f"{type(exc).__name__}: {exc}", 500)

    def do_PUT(self) -> None:
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._error("invalid JSON")
            return
        try:
            if url.path.startswith("/api/plugins/"):
                self._json(self._plugin_update(url.path, body))
            else:
                self._error("unknown endpoint", 404)
        except Exception as exc:  # noqa: BLE001
            self._error(f"{type(exc).__name__}: {exc}", 500)

    # -- handlers --------------------------------------------------------

    def _health(self) -> dict:
        cov = {}
        try:
            cov = self.app.market().coverage()
        except Exception:  # noqa: BLE001
            pass
        return {
            "status": "ok",
            "version": __version__,
            "engine_db": str(self.app.engine_db),
            "market_coverage": cov,
        }

    def _strategies(self) -> dict:
        from ..strategy import REGISTRY as STRATEGIES

        out = []
        for name in STRATEGIES.names():
            spec = STRATEGIES.get(name)
            try:
                obj = STRATEGIES.create(name)
                needs = getattr(obj, "needs_fundamentals", None)
            except Exception:  # noqa: BLE001
                needs = None
            out.append({
                "name": name,
                "origin": getattr(spec, "origin", "?"),
                "kind": getattr(spec, "kind", "strategy"),
                "needs_fundamentals": needs,
            })
        return {"strategies": out}

    def _theses(self, params: dict) -> dict:
        theses = self.app.engine().list_theses(
            market=params.get("market"),
            min_grade=params.get("min_grade"),
            limit=int(params.get("limit", 100)),
        )
        return {"theses": theses, "count": len(theses)}

    def _bars(self, params: dict) -> dict:
        """Price series for the data viewer, read on the request thread."""
        symbol = (params.get("symbol") or "").strip().upper()
        if not symbol:
            return {"error": "symbol required"}
        market = (params.get("market") or "IN").upper()
        days = max(30, min(int(params.get("days", 250)), 2000))
        return self.app.bars(symbol=symbol, market=market, days=days)

    def _symbols(self, params: dict) -> dict:
        """Symbol suggestions: union of the universe and past theses. No network."""
        market = (params.get("market") or "IN").upper()
        q = (params.get("q") or "").strip().upper()

        syms: dict[str, str] = {}  # symbol -> source

        try:
            from ..universe import load_nse, load_sp500

            uni = load_sp500() if market == "US" else load_nse("IN")
            for s in uni.symbols:
                syms.setdefault(s, "universe")
        except Exception:  # noqa: BLE001 - suggestions are best-effort
            pass

        try:
            for t in self.app.engine().list_theses(market=market, limit=500):
                syms.setdefault(t.get("symbol", ""), "thesis")
        except Exception:  # noqa: BLE001
            pass

        names = sorted(s for s in syms if s)
        if q:
            names = [s for s in names if q in s]
        return {"symbols": names[:50], "market": market, "total": len(names)}

    def _thesis(self, thesis_id: str) -> dict:
        t = self.app.engine().get_thesis(thesis_id)
        if not t:
            return {"error": "not found"}
        timeline = []
        try:
            events = self.app.ledger().events(thesis_id=thesis_id, limit=200)
            timeline = [
                {"seq": e.seq, "ts": e.ts, "kind": e.kind, "summary": e.summary}
                for e in events
            ]
        except Exception:  # noqa: BLE001
            pass

        tech = {}
        bars = []
        try:
            sym = t.get("symbol")
            if sym:
                from ..datafeed import REGISTRY as FEEDS
                from ..macro import technicals as _tech

                s = FEEDS.create("yfinance").price_series(sym, days=260)
                tech = _tech(s.bars)
                bars = [
                    {"date": b.date, "open": b.open, "high": b.high,
                     "low": b.low, "close": b.close, "volume": b.volume}
                    for b in s.bars[-120:]
                ]
        except Exception:  # noqa: BLE001 - technicals are enrichment, not required
            pass

        return {"thesis": t, "timeline": timeline,
                "technicals": tech, "bars": bars}

    def _thesis_ai(self, thesis_id: str) -> dict:
        """Plain-language reading of one thesis. Cached per thesis id."""
        import time as _time

        from .. import ai

        if not ai.enabled():
            return {"ok": False, "reason": "AI is off (set TM_AI=1 to enable)"}
        cached = self._ai_cache.get(thesis_id)
        if cached and (_time.time() - cached[0]) < 3600:
            return cached[1]
        t = self.app.engine().get_thesis(thesis_id)
        if not t:
            return {"ok": False, "reason": "thesis not found"}
        tech = {}
        try:
            sym = t.get("symbol")
            if sym:
                from ..datafeed import REGISTRY as FEEDS
                from ..macro import technicals as _tech

                s = FEEDS.create("yfinance").price_series(sym, days=120)
                tech = _tech(s.bars)
        except Exception:  # noqa: BLE001
            pass
        result = ai.interpret(t, tech)
        self._ai_cache[thesis_id] = (_time.time(), result)
        return result

    def _plugins(self, params: dict) -> dict:
        from ..plugins import all_manifests

        kind = params.get("kind")
        manifests = all_manifests()
        if kind:
            manifests = {kind: manifests.get(kind, [])}
        return {
            "plugins": {
                k: [m.to_dict() for m in ms] for k, ms in manifests.items()
            }
        }

    def _plugin_one(self, path: str) -> dict:
        from ..plugins import get_manifest

        parts = path.split("/")  # ['', 'api', 'plugins', kind, name]
        if len(parts) < 5:
            return {"error": "expected /api/plugins/<kind>/<name>"}
        kind, name = parts[3], parts[4]
        m = get_manifest(kind, name)
        if not m:
            return {"error": f"no such plugin: {kind}/{name}"}
        data = m.to_dict()
        try:
            if m.source:
                data["source_text"] = Path(m.source).read_text(encoding="utf-8")
        except OSError:
            pass
        return data

    def _plugin_create(self, body: dict) -> dict:
        from ..plugin_admin import PluginError, create
        from ..plugins import PLUGIN_KINDS

        kind = str(body.get("kind", "")).strip()
        name = str(body.get("name", "")).strip()
        if kind not in PLUGIN_KINDS:
            return {"error": f"unknown kind: {kind}"}
        try:
            path = create(kind, name, url=body.get("url", ""),
                          description=body.get("description", ""))
        except PluginError as exc:
            return {"error": str(exc)}
        return {"created": str(path), "kind": kind, "name": name}

    def _plugin_update(self, path: str, body: dict) -> dict:
        from ..plugin_admin import PluginError, update

        parts = path.split("/")
        if len(parts) < 5:
            return {"error": "expected /api/plugins/<kind>/<name>"}
        kind, name = parts[3], parts[4]
        try:
            update(kind, name,
                   description=body.get("description"),
                   enabled=body.get("enabled"))
        except PluginError as exc:
            return {"error": str(exc)}
        return {"updated": {"kind": kind, "name": name}}

    def _plugin_delete(self, path: str) -> dict:
        from ..plugin_admin import PluginError, delete

        parts = path.split("/")
        if len(parts) < 5:
            return {"error": "expected /api/plugins/<kind>/<name>"}
        kind, name = parts[3], parts[4]
        try:
            delete(kind, name)
        except PluginError as exc:
            return {"error": str(exc)}
        return {"deleted": {"kind": kind, "name": name}}

    def _plugin_toggle(self, path: str) -> dict:
        from ..plugin_admin import PluginError, validate_name
        from ..plugins import get_manifest, set_enabled

        parts = path.split("/")  # ['', 'api', 'plugins', kind, name, 'toggle']
        if len(parts) < 6:
            return {"error": "expected /api/plugins/<kind>/<name>/toggle"}
        kind, name = parts[3], parts[4]
        m = get_manifest(kind, name)
        if not m:
            return {"error": f"no such plugin: {kind}/{name}"}
        try:
            validate_name(name)
        except PluginError as exc:
            return {"error": str(exc)}
        new_state = not m.enabled
        set_enabled(kind, name, new_state)
        return {"toggled": {"kind": kind, "name": name, "enabled": new_state}}

    def _plugin_validate(self, body: dict) -> dict:
        from ..datafeed.spec_feed import FeedSpecError, parse_spec

        text = str(body.get("text", ""))
        try:
            spec = parse_spec(text)
            spec.validate()
        except FeedSpecError as exc:
            return {"valid": False, "error": str(exc)}
        return {"valid": True, "name": spec.name}

    def _ledger(self, params: dict) -> dict:
        n = int(params.get("limit", 50))
        events = self.app.ledger().tail(n)
        return {
            "events": [
                {"seq": e.seq, "ts": e.ts, "kind": e.kind,
                 "subject": e.subject, "summary": e.summary}
                for e in reversed(events)
            ],
            "count": self.app.ledger().count(),
        }

    def _scan(self, body: dict) -> dict:
        from ..strategy import REGISTRY as STRATEGIES

        strategies = body.get("strategies") or STRATEGIES.names() or ["canslim"]
        market = body.get("market", "US")
        limit = body.get("limit")
        limit = int(limit) if limit else None

        gate = self._session_gate(market, body)
        if gate:
            return gate

        symbols = body.get("symbols")
        if symbols:
            if isinstance(symbols, list):
                syms = [s.strip().upper() for s in symbols if str(s).strip()]
            else:
                syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
        else:
            from ..universe import build_plan, load_nse, load_sp500

            uni = load_sp500() if market == "US" else load_nse("IN")
            syms = [i.symbol for i in build_plan(uni, limit=limit).items]

        return self.app.run_scan(
            strategy_names=strategies, symbols=syms, market=market, limit=limit
        )

        return self.app.run_scan(
            strategy_names=strategies, symbols=syms, market=market, limit=limit
        )

    def _session_gate(self, market: str, body: dict) -> dict | None:
        """Refuse a scan outside the market's session unless the caller forces it.

        Data is still fetchable when a market is closed, but a scan run then
        produces stale-looking signals; requiring an explicit `force` keeps the
        default behaviour honest. Returns None to proceed.
        """
        if body.get("force"):
            return None
        from ..market_hours import session_status

        st = session_status(market)
        if st.get("is_open"):
            return None
        return {
            "started": False,
            "reason": f"{market} market is {st['status']} ({st['note']}); "
                      f"pass force=true to scan anyway",
            "session": st,
        }

    def _scan_all(self, body: dict) -> dict:
        from ..strategy import REGISTRY as STRATEGIES

        strategies = body.get("strategies") or STRATEGIES.names() or ["canslim"]
        market = body.get("market", "US")
        limit = body.get("limit")
        limit = int(limit) if limit else None

        gate = self._session_gate(market, body)
        if gate:
            return gate

        return self.app.start_universe_scan(
            strategy_names=strategies, market=market, limit=limit
        )


def serve(*, host: str = "127.0.0.1", port: int = 8787,
          open_browser: bool = True, **db_kwargs) -> int:
    from ..bootstrap import bootstrap_all

    report = bootstrap_all()
    app = App(**db_kwargs)

    class BoundHandler(Handler):
        def __init__(self, *a, **kw):
            self.app = app
            super().__init__(*a, **kw)

    httpd = ThreadingHTTPServer((host, port), BoundHandler)
    url = f"http://{host}:{port}/"
    print(f"trade-monitor dashboard: {url}")
    print(f"  strategies: {', '.join(report['strategies']['loaded']) or 'none'}")
    print(f"  feeds: {', '.join(f for f in report['feeds'] if f) or 'builtin only'}")
    print(f"  engine db: {app.engine_db}")
    print(f"  market db: {app.market_db}")
    print("  Ctrl-C to stop")
    if open_browser:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        httpd.server_close()
    return 0


__all__ = ["App", "Handler", "serve"]
