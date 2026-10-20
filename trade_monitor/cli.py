"""trade-monitor command line.

One entry point for everything: scan, serve, strategies, data, ledger, backtest.

    trade-monitor scan   --strategy canslim --market IN
    trade-monitor serve  --port 8787
    trade-monitor strategies
    trade-monitor seed   --market IN --limit 50

The CLI is a thin shell over the same engine the dashboard and the scheduler
call, so a scan behaves identically however it is started.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__


def _default_paths() -> dict[str, Path]:
    base = Path("~/.trade-monitor").expanduser()
    return {
        "base": base,
        "engine": base / "engine.db",
        "market": base / "market.db",
        "ledger": base / "ledger.db",
        "strategies": base / "strategies",
    }


def cmd_scan(args: argparse.Namespace) -> int:
    from .engine import run
    from .strategy import REGISTRY as STRATEGIES
    from .strategy.loader import discover
    from .store import Ledger, MarketStore, Store
    from .universe import build_plan, load_nse, load_sp500

    p = _default_paths()
    store = Store(p["engine"] if not args.db else args.db)
    ledger = Ledger.at(p["ledger"])
    discover(STRATEGIES, [Path(args.strategies) if args.strategies else Path("strategies")])

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        uni = load_sp500() if args.market == "US" else load_nse("IN")
        plan = build_plan(uni, limit=args.limit)
        symbols = [item.symbol for item in plan.items]

    from .datafeed import REGISTRY as FEEDS

    feed = FEEDS.create("yfinance")
    ledger.write(
        "run.started",
        subject="scan",
        summary=f"scan {len(symbols)} symbols with {args.strategy}",
    )

    reports = run(
        strategy_names=args.strategy,
        symbols=symbols,
        feed=feed,
        market=args.market,
        store=store,
        ledger=ledger,
        workers=args.workers,
    )

    total_hits = 0
    for name, rep in reports.items():
        print(f"\n== {name} ==")
        print(rep.summary())
        for t in rep.top(args.top):
            print(f"   {t.symbol:14s} {getattr(t, 'grade', '?'):2s} "
                  f"conf={getattr(t, 'confidence', 0)}")
        total_hits += rep.hits

    ledger.write(
        "run.finished",
        subject="scan",
        summary=f"scan finished: {total_hits} hits",
    )
    store.close()
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import serve
    from .strategy import REGISTRY as STRATEGIES
    from .strategy.loader import discover

    discover(STRATEGIES, [Path(args.strategies) if args.strategies else Path("strategies")])
    return serve(host=args.host, port=args.port, open_browser=not args.no_browser)


def cmd_strategies(args: argparse.Namespace) -> int:
    from .strategy import REGISTRY as STRATEGIES
    from .strategy.loader import discover, LoadReport

    dirs = [Path(args.strategies)] if args.strategies else None
    report: LoadReport = discover(STRATEGIES, dirs)
    print(report.summary())
    print()
    for name in STRATEGIES.names():
        spec = STRATEGIES.get(name)
        try:
            obj = STRATEGIES.create(name)
            needs = getattr(obj, "needs_fundamentals", "?")
        except Exception:  # noqa: BLE001
            needs = "?"
        print(f"  {name:24s} needs_fundamentals={needs}  v{spec.version}")
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    """Pull data into the store so the dashboard has something to show."""
    from .datafeed import REGISTRY as FEEDS
    from .store import MarketStore
    from .universe import build_plan, load_nse, load_sp500

    p = _default_paths()
    ms = MarketStore(p["market"] if not args.db else args.db)
    feed = FEEDS.create("yfinance")

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        uni = load_sp500() if args.market == "US" else load_nse("IN")
        plan = build_plan(uni, limit=args.limit)
        symbols = [item.symbol for item in plan.items]

    print(f"seeding {len(symbols)} symbols into {ms.path}")
    ok = failed = 0
    for i, sym in enumerate(symbols, 1):
        try:
            ps = feed.price_series(sym, args.days)
            if not ps or not ps.bars:
                failed += 1
                continue
            ms.put_bars(sym, ps.bars, source="yfinance")
            try:
                f = feed.fundamentals(sym)
                ms.put_eps(sym, f.quarterly_eps, period="quarter", source="yfinance")
                ms.put_eps(sym, f.annual_eps, period="annual", source="yfinance")
                ms.put_meta(sym, market=args.market)
            except Exception:
                pass
            ok += 1
            if i % 25 == 0 or i == len(symbols):
                print(f"  {i}/{len(symbols)}  ok={ok} failed={failed}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  {sym}: {type(exc).__name__}: {exc}")
    print("\ncoverage:", json.dumps(ms.coverage(), indent=2))
    ms.close()
    return 0


def cmd_ledger(args: argparse.Namespace) -> int:
    from .store import Ledger

    p = _default_paths()
    led = Ledger.at(p["ledger"] if not args.db else args.db)
    if args.verify:
        ok = led.verify() if hasattr(led, "verify") else None
        print("ledger verify:", "OK" if ok else "not implemented")
        return 0
    for e in led.tail(args.limit):
        print(f"{e.seq:6d}  {e.ts}  {e.kind:20s}  {e.summary}")
    print(f"\n{led.count()} events, kinds: {[k for k, _ in led.kinds()]}")
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    print(f"trade-monitor {__version__}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trade-monitor",
        description="Self-hosted trade monitor. Bring your own strategy.",
    )
    parser.add_argument("--version", action="version", version=f"trade-monitor {__version__}")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("scan", help="run strategies over a universe")
    p.add_argument("--strategy", "-s", action="append", default=None,
                   help="strategy name (repeatable); default: all loaded")
    p.add_argument("--market", "-m", default="IN", choices=["IN", "US"])
    p.add_argument("--symbols", help="comma-separated symbols (overrides universe)")
    p.add_argument("--limit", type=int, default=None, help="max symbols from universe")
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--db", help="engine db path")
    p.add_argument("--strategies", help="strategy directory to scan")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("serve", help="start the dashboard")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--strategies", help="strategy directory to scan")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("strategies", help="list loaded strategies")
    p.add_argument("--strategies", help="strategy directory to scan")
    p.set_defaults(func=cmd_strategies)

    p = sub.add_parser("seed", help="pull data into the market store")
    p.add_argument("--market", "-m", default="IN", choices=["IN", "US"])
    p.add_argument("--symbols", help="comma-separated symbols")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--days", type=int, default=400)
    p.add_argument("--db", help="market db path")
    p.set_defaults(func=cmd_seed)

    p = sub.add_parser("ledger", help="show the audit trail")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--verify", action="store_true", help="verify the hash chain")
    p.add_argument("--db", help="ledger db path")
    p.set_defaults(func=cmd_ledger)

    p = sub.add_parser("version", help="print version")
    p.set_defaults(func=cmd_version)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    if args.command == "scan" and not args.strategy:
        from .strategy import REGISTRY as STRATEGIES
        args.strategy = STRATEGIES.names() or ["canslim"]
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
