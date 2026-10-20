"""The plugin manifest: a uniform view of every plugin, of every kind.

The console and API never care whether a plugin is code or a spec, built-in or
user-supplied. They ask this module for **manifests** and get a consistent
shape. This is what makes one console able to manage all five kinds.

A manifest is built **without trusting the plugin**:

  * code plugins -- the registry spec (name, version, description, requires,
    config_schema) is authoritative; we do not execute the plugin to describe it
  * spec plugins -- the parsed TOML is authoritative

`status` is one of:
  * `ok`        -- loaded and enabled
  * `disabled`  -- present but turned off (skipped at load)
  * `error`     -- present but failed to load; `reason` explains why, and the
                   plugin is still listed so it never vanishes silently
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# The five kinds and where each kind's files live. `suffix` is the file
# extension discovery looks for; `.py` kinds are code, `.toml` kinds are specs.
PLUGIN_KINDS: dict[str, dict[str, Any]] = {
    "strategy":  {"dirs": ("strategies", "~/.trade-monitor/strategies",
                           "~/.config/trade-monitor/strategies"), "suffix": ".py"},
    "feed":      {"dirs": ("feeds", "~/.trade-monitor/feeds",
                           "~/.config/trade-monitor/feeds"), "suffix": ".toml"},
    "pattern":   {"dirs": ("patterns", "~/.trade-monitor/patterns",
                           "~/.config/trade-monitor/patterns"), "suffix": ".py"},
    "validator": {"dirs": ("validators", "~/.trade-monitor/validators",
                           "~/.config/trade-monitor/validators"), "suffix": ".py"},
    "delivery":  {"dirs": ("deliverers", "~/.trade-monitor/deliverers",
                           "~/.config/trade-monitor/deliverers"), "suffix": ".py"},
}


@dataclass
class Manifest:
    name: str
    kind: str
    origin: str = "builtin"          # "builtin" | "code" | "spec"
    version: str = "0.0.0"
    enabled: bool = True
    status: str = "ok"               # ok | disabled | error
    description: str = ""
    requires: list[str] = field(default_factory=list)
    provides: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    config_schema: dict[str, Any] = field(default_factory=dict)
    source: str = ""                 # file path, when loadable from disk
    reason: str = ""                 # populated when status == "error"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "kind": self.kind, "origin": self.origin,
            "version": self.version, "enabled": self.enabled, "status": self.status,
            "description": self.description, "requires": self.requires,
            "provides": self.provides, "params": self.params,
            "config_schema": self.config_schema, "source": self.source,
            "reason": self.reason,
        }


# --------------------------------------------------------------------------
# Disabled set: which plugins a user has turned off, persisted beside the DBs
# --------------------------------------------------------------------------


def _state_path(base: str | Path | None = None) -> Path:
    root = Path(base).expanduser() if base else Path("~/.trade-monitor").expanduser()
    return root / "plugins.disabled.json"


def load_disabled(base: str | Path | None = None) -> set[tuple[str, str]]:
    """Return {(kind, name)} that are disabled. Missing file -> nothing disabled."""
    p = _state_path(base)
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return {(str(k), str(n)) for k, n in data.get("disabled", [])}
    except (OSError, json.JSONDecodeError, ValueError):
        return set()


def save_disabled(disabled: set[tuple[str, str]], base: str | Path | None = None) -> None:
    p = _state_path(base)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"disabled": sorted([k, n] for k, n in disabled)}
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def set_enabled(kind: str, name: str, enabled: bool, base: str | Path | None = None) -> None:
    disabled = load_disabled(base)
    if enabled:
        disabled.discard((kind, name))
    else:
        disabled.add((kind, name))
    save_disabled(disabled, base)


# --------------------------------------------------------------------------
# Discovery -- spec plugins from disk (code plugins come from the registries)
# --------------------------------------------------------------------------


def plugin_dirs(kind: str, base: str | Path | None = None) -> tuple[Path, ...]:
    """Directories discovery scans for a kind.

    With a `base`, only `<base>/<kind-dir>` is scanned -- used by tests and by
    callers that want an isolated root. Without one, the shipped + per-user dirs.
    """
    info = PLUGIN_KINDS[kind]
    if base is not None:
        leaf = info["dirs"][1].split("/")[-1]
        return (Path(base).expanduser() / leaf,)
    return tuple(Path(d).expanduser() for d in info["dirs"])


def scan_spec_manifests(
    kind: str = "feed",
    *,
    base: str | Path | None = None,
) -> list[Manifest]:
    """Read every spec file of a kind and return manifests, never raising."""
    from .datafeed.spec_feed import FeedSpecError, load_spec_file

    info = PLUGIN_KINDS.get(kind)
    if not info:
        return []
    disabled = load_disabled(base)
    out: list[Manifest] = []
    seen: set[str] = set()

    for d in plugin_dirs(kind, base):
        if not d.is_dir():
            continue
        for path in sorted(d.glob(f"*{info['suffix']}")):
            if path.name.startswith("_"):
                continue
            try:
                spec = load_spec_file(path)
                spec.validate()
                m = Manifest(
                    name=spec.name, kind=kind, origin="spec",
                    version=spec.version,
                    enabled=spec.enabled and (kind, spec.name) not in disabled,
                    description=spec.description,
                    params={"url": spec.url, "method": spec.method,
                            "auth_env": spec.auth_env, "markets": spec.markets},
                    config_schema={}, source=str(path),
                )
                if not m.enabled:
                    m.status = "disabled"
                out.append(m)
                seen.add(spec.name)
            except FeedSpecError as exc:
                out.append(Manifest(
                    name=path.stem, kind=kind, origin="spec", enabled=False,
                    status="error", source=str(path), reason=str(exc),
                ))
    return out


def registry_manifests(kind: str, registry: Any, *, base: str | Path | None = None) -> list[Manifest]:
    """Turn a runtime Registry into manifests for a kind."""
    disabled = load_disabled(base)
    out: list[Manifest] = []
    for spec in registry.describe():
        name = spec["name"]
        is_disabled = (kind, name) in disabled
        out.append(Manifest(
            name=name, kind=kind, origin="builtin",
            version=spec.get("version", "0.0.0"),
            enabled=not is_disabled,
            status="disabled" if is_disabled else "ok",
            description=spec.get("description", ""),
            requires=list(spec.get("requires", [])),
            provides=list(spec.get("provides", [])),
            config_schema=spec.get("config_schema", {}) or {},
        ))
    return out


def all_manifests(*, base: str | Path | None = None) -> dict[str, list[Manifest]]:
    """Every plugin of every kind, for the console. Fault-isolated per kind."""
    from .datafeed import REGISTRY as FEEDS
    from .delivery import REGISTRY as DELIVERY
    from .patterns import REGISTRY as PATTERNS
    from .strategy import REGISTRY as STRATEGIES
    from .validate import REGISTRY as VALIDATORS

    registries = {
        "strategy": STRATEGIES, "feed": FEEDS, "pattern": PATTERNS,
        "validator": VALIDATORS, "delivery": DELIVERY,
    }
    result: dict[str, list[Manifest]] = {}
    for kind, reg in registries.items():
        try:
            manifests = registry_manifests(kind, reg, base=base)
        except Exception:  # noqa: BLE001 - a bad registry must not hide the rest
            manifests = []
        # Spec plugins (currently feeds) are added on top and may shadow code
        # plugins of the same name -- later wins, like directory loading.
        if PLUGIN_KINDS[kind]["suffix"] == ".toml":
            spec_names = {m.name for m in scan_spec_manifests(kind, base=base)}
            manifests = [m for m in manifests if m.name not in spec_names]
            manifests += scan_spec_manifests(kind, base=base)
        result[kind] = sorted(manifests, key=lambda m: m.name)
    return result


def get_manifest(kind: str, name: str, *, base: str | Path | None = None) -> Manifest | None:
    for m in all_manifests(base=base).get(kind, []):
        if m.name == name:
            return m
    return None


__all__ = [
    "PLUGIN_KINDS", "Manifest", "all_manifests", "get_manifest",
    "scan_spec_manifests", "registry_manifests",
    "load_disabled", "save_disabled", "set_enabled",
]
