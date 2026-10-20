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
        """Run a scan on a background thread so the request returns at once."""
        from ..datafeed import REGISTRY as FEEDS
        from ..engine import run
        from ..store import Store

        def _work():
            with self._lock:
                store = Store(self.engine_db)
                feed = FEEDS.create("yfinance")
                run(
                    strategy_names=strategy_names,
                    symbols=symbols,
                    feed=feed,
                    market=market,
                    store=store,
                    ledger=self.ledger(),
                )
                store.close()

        t = threading.Thread(target=_work, daemon=True)
        t.start()
        return {"started": True, "symbols": len(symbols)}


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
            elif path.startswith("/api/thesis/"):
                self._json(self._thesis(path.rsplit("/", 1)[-1]))
            elif path == "/api/runs":
                self._json(self.app.engine().recent_runs(int(params.get("limit", 20))))
            elif path == "/api/ledger":
                self._json(self._ledger(params))
            elif path == "/api/coverage":
                self._json(self.app.market().coverage())
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
        return {"thesis": t, "timeline": timeline}

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
        market = body.get("market", "IN")
        symbols = body.get("symbols")
        limit = int(body.get("limit", 25))

        if symbols:
            syms = [s.strip().upper() for s in symbols if s.strip()] \
                if isinstance(symbols, list) else \
                [s.strip().upper() for s in symbols.split(",") if s.strip()]
        else:
            from ..universe import build_plan, load_nse, load_sp500

            uni = load_sp500() if market == "US" else load_nse("IN")
            syms = [i.symbol for i in build_plan(uni, limit=limit).items]

        return self.app.run_scan(
            strategy_names=strategies, symbols=syms, market=market, limit=limit
        )


def serve(*, host: str = "127.0.0.1", port: int = 8787,
          open_browser: bool = True, **db_kwargs) -> int:
    app = App(**db_kwargs)

    class BoundHandler(Handler):
        def __init__(self, *a, **kw):
            self.app = app
            super().__init__(*a, **kw)

    httpd = ThreadingHTTPServer((host, port), BoundHandler)
    url = f"http://{host}:{port}/"
    print(f"trade-monitor dashboard: {url}")
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
