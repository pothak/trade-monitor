"""Console CRUD for plugins.

The console can create, edit and remove plugins, but it never becomes an IDE:
creating a **code** plugin writes a minimal, valid starter file that the user
then edits in their own editor. Creating a **spec** plugin (feeds) writes the
`.toml` from structured values. Neither path executes user code.

Safety rules, all enforced here:

  * a plugin name must be a safe identifier -- no path traversal, no dotted
    names, no leading underscore
  * writes are confined to the per-user plugin directory
    (~/.trade-monitor/<kind-dir>/), never the shipped repo
  * deleting and editing are restricted to the per-user directory; a built-in
    plugin cannot be deleted from the console
  * malformed specs are rejected before anything is written
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .plugins import PLUGIN_KINDS, set_enabled

_SAFE_NAME = re.compile(r"^[a-z][a-z0-9_]{1,40}$")

# One starter template per code kind. Small, valid, and obviously meant to be
# edited. These are the only code the console ever writes.
CODE_TEMPLATES: dict[str, str] = {
    "strategy": '''"""A strategy. Edit this file, then reload the console."""

from trade_monitor_contracts import Evidence, Invalidation, Thesis


class {Class}:
    name = "{name}"
    version = "0.1.0"
    requires: tuple[str, ...] = ()          # pattern names, or ()
    needs_fundamentals = False              # flip to True to fetch fundamentals

    def score(self, series, fundamentals, patterns=None, **options):
        # Return (evidence, grade, confidence 0-100).
        return [], "C", 50

    def thesis(self, *, series, fundamentals, patterns=None,
               evidence, grade, confidence, **options):
        return Thesis(
            id="{name}:" + series.symbol + ":" + (series.last.date if series.last else ""),
            symbol=series.symbol, name=series.symbol, market=series.market,
            currency=series.currency, as_of=series.last.date if series.last else "",
            strategy=self.name, grade=grade, confidence=confidence,
            evidence=list(evidence),
            invalidation=Invalidation(rule="edit me", time_stop_days=5),
        )

    def narrative(self, thesis) -> str:
        return f"{{thesis.symbol}}: {{thesis.grade}}"


STRATEGY = {Class}()
''',
    "pattern": '''"""A pattern detector. Edit this file, then reload the console."""


class {Class}:
    name = "{name}"
    provides: tuple[str, ...] = ()

    def detect(self, series, **options):
        # Return a dict of measured geometry, or None.
        return None
''',
    "validator": '''"""A thesis validator. Edit this file, then reload the console."""

from trade_monitor_contracts import ValidationResult


class {Class}:
    name = "{name}"
    kind = "rule"          # "rule" or "model"

    def validate(self, thesis):
        return ValidationResult(
            validator=self.name, kind=self.kind, passed=True,
        )
''',
    "delivery": '''"""A delivery destination. Edit this file, then reload the console."""


class {Class}:
    name = "{name}"

    def deliver(self, thesis, **options) -> bool:
        return True
''',
}

FEED_TEMPLATE = '''# A declarative data feed. Interpreted by the generic "rest" driver.
# No code is executed; this file only describes one HTTP endpoint.

[feed]
name = "{name}"
kind = "rest"
description = "{description}"
enabled = true

[feed.http]
url = "{url}"
method = "GET"
# auth_header = "Authorization"
# auth_env = "MY_TOKEN"        # token is read from this env var, never stored

[feed.map]
bars = "$.candles"             # path to the list of bars in the response
date = "t"
open = "o"
high = "h"
low = "l"
close = "c"
volume = "v"
date_format = "iso"            # "iso" or "epoch"

[feed.markets]
IN = "^NSEI"
US = "^GSPC"
'''


class PluginError(ValueError):
    """A CRUD operation was invalid; surfaced to the console as a 4xx."""


def user_dir(kind: str, base: str | Path | None = None) -> Path:
    """The per-user directory the console writes into. Created on demand."""
    if kind not in PLUGIN_KINDS:
        raise PluginError(f"unknown plugin kind: {kind}")
    root = Path(base).expanduser() if base else Path("~/.trade-monitor").expanduser()
    d = root / PLUGIN_KINDS[kind]["dirs"][1].split("/")[-1]
    return d


def validate_name(name: str) -> str:
    name = (name or "").strip().lower()
    if not _SAFE_NAME.match(name):
        raise PluginError(
            "name must be 2-41 chars: lowercase letters, digits, underscore; "
            "start with a letter"
        )
    return name


def _class_name(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_")) or "Plugin"


def _target(kind: str, name: str, base: str | Path | None = None) -> Path:
    suffix = PLUGIN_KINDS[kind]["suffix"]
    return user_dir(kind, base) / f"{name}{suffix}"


def create(
    kind: str,
    name: str,
    *,
    base: str | Path | None = None,
    **fields: Any,
) -> Path:
    """Write a new plugin file. Returns its path. Never overwrites."""
    name = validate_name(name)
    if kind not in PLUGIN_KINDS:
        raise PluginError(f"unknown plugin kind: {kind}")

    path = _target(kind, name, base)
    if path.exists():
        raise PluginError(f"{kind} '{name}' already exists")

    if kind == "feed":
        url = str(fields.get("url", "")).strip()
        if not url:
            raise PluginError("feed requires a url")
        text = FEED_TEMPLATE.format(
            name=name, url=url,
            description=str(fields.get("description", "")),
        )
        _validate_feed_text(text)
    else:
        template = CODE_TEMPLATES.get(kind)
        if template is None:
            raise PluginError(f"cannot scaffold a '{kind}' plugin")
        text = template.format(name=name, Class=_class_name(name))

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _validate_feed_text(text: str) -> None:
    from .datafeed.spec_feed import FeedSpecError, parse_spec

    try:
        parse_spec(text).validate()
    except FeedSpecError as exc:
        raise PluginError(f"invalid feed spec: {exc}") from exc


def update(
    kind: str,
    name: str,
    *,
    base: str | Path | None = None,
    description: str | None = None,
    enabled: bool | None = None,
) -> None:
    """Edit the small mutable surface of a plugin: description and enabled.

    Source is intentionally not edited here -- that stays in the user's editor.
    """
    name = validate_name(name)
    if enabled is not None:
        set_enabled(kind, name, enabled, base=base)
    if description is not None:
        path = _target(kind, name, base)
        if path.exists() and kind == "feed":
            text = path.read_text(encoding="utf-8")
            text = _replace_description(text, description)
            path.write_text(text, encoding="utf-8")


def _replace_description(text: str, description: str) -> str:
    out: list[str] = []
    done = False
    for line in text.splitlines():
        if not done and line.strip().startswith("description"):
            out.append(f'description = "{description}"')
            done = True
        else:
            out.append(line)
    if not done:
        out.append(f'description = "{description}"')
    return "\n".join(out) + "\n"


def delete(kind: str, name: str, *, base: str | Path | None = None) -> None:
    """Remove a plugin file. Only user-dir (non-builtin) plugins are deletable."""
    name = validate_name(name)
    path = _target(kind, name, base)
    if not path.exists():
        raise PluginError(
            f"{kind} '{name}' is built-in or does not exist; only plugins in "
            f"{user_dir(kind, base)} can be deleted"
        )
    path.unlink()
    set_enabled(kind, name, True, base=base)  # clear any stale disabled flag


__all__ = [
    "PluginError", "create", "update", "delete", "user_dir", "validate_name",
    "CODE_TEMPLATES", "FEED_TEMPLATE",
]
