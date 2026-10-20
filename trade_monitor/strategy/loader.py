"""Runtime strategy injection.

Strategies are authored outside the package and loaded at runtime from
configured directories. Nothing about a strategy is baked into the monitor.

Discovery
---------
A directory is scanned for `*.py` files. Each file may expose a strategy in one
of two ways:

    # a bare strategy object
    STRATEGY = MyStrategy()

    # or several
    STRATEGIES = {"my_strategy": MyStrategy()}

    # or a registration hook, which receives the registry
    def register(registry):
        registry.register("my_strategy", MyStrategy, requires=(...))

A file that exposes more than one is fine. A file that exposes none is skipped
and reported, not fatal.

Isolation
---------
Every step is guarded. A file that fails to import, a class that is malformed,
or a strategy that raises while being inspected is skipped, and the reason is
written to the event ledger. The monitor keeps running with whatever loaded
successfully. This follows the same rule as the rest of the codebase: one bad
component must never take down a scan.

Injection order
---------------
Later directories override earlier ones by name, and the packaged reference
strategies load first. So a user can shadow a shipped strategy simply by giving
theirs the same name in a directory they control. The ledger records both the
shadowing and the shadowed.
"""

from __future__ import annotations

import importlib.util
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable

from ..registry import Registry

# Directories scanned by default, in increasing priority.
DEFAULT_STRATEGY_DIRS: tuple[str, ...] = (
    "strategies",                      # relative to the project root
    "~/.trade-monitor/strategies",     # per-user
    "~/.config/trade-monitor/strategies",
)


@dataclass
class LoadReport:
    """What happened during one discovery pass. Everything here is auditable."""

    loaded: list[str] = field(default_factory=list)
    replaced: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)   # (origin, reason)
    files: list[str] = field(default_factory=list)
    dirs: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.loaded)

    def summary(self) -> dict[str, Any]:
        return {
            "loaded": self.loaded,
            "replaced": self.replaced,
            "skipped": [{"origin": o, "reason": r} for o, r in self.skipped],
            "files_scanned": len(self.files),
            "dirs": self.dirs,
        }


def _module_name(path: Path) -> str:
    """A unique, stable module name for a file, so re-loading does not collide."""
    safe = "".join(c if c.isalnum() else "_" for c in str(path.with_suffix("")))
    return f"tm_strategy_{safe}"


def _load_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(_module_name(path), path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot build import spec for {path}")
    module = importlib.util.module_from_spec(spec)
    # Register before exec so a strategy can import itself / use decorators.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return module


def _is_strategy(obj: Any) -> bool:
    """Duck-typed check: a strategy needs the methods the engine will call."""
    return all(
        hasattr(obj, attr)
        for attr in ("name", "requires", "score", "thesis", "narrative")
    )


def _extract(module: ModuleType) -> list[tuple[str, Any]]:
    """Pull candidate strategies out of a loaded module."""
    found: list[tuple[str, Any]] = []

    obj = getattr(module, "STRATEGY", None)
    if obj is not None and _is_strategy(obj):
        found.append((obj.name, obj))

    mapping = getattr(module, "STRATEGIES", None)
    if isinstance(mapping, dict):
        for key, val in mapping.items():
            if _is_strategy(val):
                found.append((str(key), val))

    return found


def load_strategy_file(
    path: Path,
    registry: Registry[Any],
    *,
    report: LoadReport,
) -> None:
    """Load one file into the registry. Never raises."""
    report.files.append(str(path))
    try:
        module = _load_module(path)
    except Exception as exc:  # noqa: BLE001
        report.skipped.append(
            (str(path), f"import failed: {type(exc).__name__}: {exc}".split("\n")[0])
        )
        return

    # Registration hook takes precedence: it is the explicit interface.
    hook = getattr(module, "register", None)
    if callable(hook):
        try:
            before = set(registry.names())
            hook(registry)
            for name in set(registry.names()) - before:
                report.loaded.append(name)
            return
        except Exception as exc:  # noqa: BLE001
            report.skipped.append(
                (str(path), f"register() failed: {type(exc).__name__}: {exc}")
            )
            return

    candidates = _extract(module)
    if not candidates:
        report.skipped.append(
            (str(path), "no STRATEGY, STRATEGIES or register() found")
        )
        return

    for name, obj in candidates:
        try:
            if name in registry:
                # Later wins: this is deliberate shadowing, and it is recorded.
                report.replaced.append(name)
                registry._specs.pop(name, None)
            requires = tuple(getattr(obj, "requires", ()) or ())
            registry.register(
                name,
                lambda _obj=obj: _obj,
                description=getattr(obj, "__doc__", "") or "runtime-injected strategy",
                version=getattr(obj, "version", "0.0.0"),
                requires=requires,
            )
            report.loaded.append(name)
        except Exception as exc:  # noqa: BLE001
            report.skipped.append(
                (str(path), f"registering {name!r} failed: {type(exc).__name__}: {exc}")
            )


def discover(
    registry: Registry[Any],
    directories: Iterable[str | Path] | None = None,
    *,
    ledger: Any | None = None,
    run_id: int | None = None,
) -> LoadReport:
    """Scan directories and inject every strategy found into `registry`.

    Returns a LoadReport and, if a ledger is given, writes one `strategy.loaded`
    or `strategy.skipped` event per outcome so the injection is auditable.
    """
    report = LoadReport()
    dirs = list(directories) if directories is not None else list(DEFAULT_STRATEGY_DIRS)

    for raw in dirs:
        d = Path(raw).expanduser()
        report.dirs.append(str(d))
        if not d.is_dir():
            continue
        for path in sorted(d.glob("*.py")):
            if path.name.startswith("_"):
                continue
            before_loaded = len(report.loaded)
            before_skipped = len(report.skipped)
            load_strategy_file(path, registry, report=report)

            if ledger is not None:
                for name in report.loaded[before_loaded:]:
                    ledger.write(
                        "strategy.loaded", subject=name, run_id=run_id,
                        summary=f"strategy {name!r} injected from {path}",
                        payload={"origin": str(path), "name": name},
                    )
                for origin, reason in report.skipped[before_skipped:]:
                    ledger.write(
                        "strategy.skipped", subject=Path(origin).stem, run_id=run_id,
                        summary=f"strategy file skipped: {reason}",
                        payload={"origin": origin, "reason": reason},
                    )

    if ledger is not None:
        for name in report.replaced:
            ledger.write(
                "strategy.shadowed", subject=name, run_id=run_id,
                summary=f"injected strategy {name!r} replaced a previously registered one",
                payload={"name": name},
            )
    return report


def reload(registry: Registry[Any], **kwargs: Any) -> LoadReport:
    """Re-discover from scratch, reflecting edits and deletions on disk.

    The packaged reference strategies are re-registered, then injected files are
    loaded on top. A directory-scanned file that shadowed a reference strategy
    only re-shadows it if it is still on disk.
    """
    from . import load_reference

    registry._specs.clear()
    load_reference()
    return discover(registry, **kwargs)


__all__ = [
    "discover", "reload", "load_strategy_file", "LoadReport",
    "DEFAULT_STRATEGY_DIRS",
]
