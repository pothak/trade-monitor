"""Data feeds.

Two ways a feed exists:

  * **code** -- a class implementing the DataFeed protocol, registered by name
    (yfinance today).
  * **spec** -- a `.toml` file describing a REST endpoint, interpreted by the
    generic `SpecFeed` driver (registered as "rest").

The engine selects a feed by name and never cares which of the two it is.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..registry import Registry
from .spec_feed import (
    FEED_SPEC_DIRS,
    FeedSpec,
    FeedSpecError,
    SpecFeed,
    load_spec_file,
    parse_spec,
)
from .yfinance_feed import REGISTRY as YFINANCE_REGISTRY

REGISTRY = YFINANCE_REGISTRY

# The generic driver. A spec feed is instantiated by handing its parsed spec to
# this factory, so `REGISTRY.create("rest", spec=...)` yields a working feed.
REGISTRY.register(
    "rest",
    lambda *, spec: SpecFeed(spec),
    description="Generic declarative REST feed. Interprets a feed.toml; no code.",
    version="0.1.0",
    provides=("price",),
    config_schema={
        "url": {"type": "string", "required": True},
        "field_map": {"type": "object"},
    },
)


def discover_feeds(
    registry: Registry[Any] | None = None,
    directories: list[str | Path] | None = None,
) -> list[dict[str, Any]]:
    """Scan feed spec directories; register each valid spec as a named feed.

    A malformed spec is recorded (status=error), never fatal. Later dirs shadow
    earlier ones by name. Returns manifests for the console.
    """
    reg = registry or REGISTRY
    dirs = directories if directories is not None else list(FEED_SPEC_DIRS)
    manifests: list[dict[str, Any]] = []
    seen: dict[str, str] = {}

    for raw in dirs:
        d = Path(raw).expanduser()
        if not d.is_dir():
            continue
        for path in sorted(d.glob("*.toml")):
            try:
                spec = load_spec_file(path)
                spec.validate()
            except FeedSpecError as exc:
                manifests.append({
                    "name": path.stem, "kind": "feed", "origin": "spec",
                    "enabled": False, "status": "error", "description": "",
                    "params": {}, "config_schema": {},
                    "source": str(path), "reason": str(exc),
                })
                continue

            if spec.name in seen:
                # Shadow: drop the earlier registration, keep the later.
                reg._specs.pop(spec.name, None)
            seen[spec.name] = str(path)

            if not spec.enabled:
                manifests.append({**spec.to_manifest()})
                continue

            if spec.name not in reg:
                reg.register(
                    spec.name,
                    lambda *, _spec=spec, **kw: SpecFeed(_spec, **kw),
                    description=spec.description or f"spec feed from {path.name}",
                    version=spec.version,
                    provides=("price",),
                )
            manifests.append(spec.to_manifest())
    return manifests


__all__ = [
    "REGISTRY", "discover_feeds", "SpecFeed", "FeedSpec", "FeedSpecError",
    "parse_spec", "load_spec_file", "FEED_SPEC_DIRS",
]
