"""Delivery adapters (DEL-1, DEL-2).

Delivery renders a finished thesis to a destination. It never fetches or
recomputes data -- it only formats and emits what the engine already decided.

Registered adapters:

  * `markdown_file` -- append theses to a markdown file (the dev sink)
  * `console`       -- print a one-line summary per thesis
  * `jsonl_file`    -- append one JSON object per thesis, for tooling

Each implements the DeliveryAdapter protocol:

    name: str
    deliver(thesis, **options) -> bool
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from trade_monitor_contracts import Thesis

from ..registry import Registry


def _grade_badge(grade: str) -> str:
    return f"[{grade}]"


class ConsoleDelivery:
    name = "console"

    def deliver(self, thesis: Thesis, **options: Any) -> bool:
        line = (
            f"{_grade_badge(thesis.grade)} {thesis.symbol:14s} "
            f"conf={thesis.confidence:3d}  {thesis.strategy}  {thesis.as_of}"
        )
        print(line)
        return True


class MarkdownFileDelivery:
    """Append a thesis to a markdown file. The default dev sink."""

    name = "markdown_file"

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path).expanduser() if path else (
            Path("~/.trade-monitor/theses.md").expanduser()
        )

    def deliver(self, thesis: Thesis, **options: Any) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(self.render(thesis))
        return True

    @staticmethod
    def render(thesis: Thesis) -> str:
        lines = [
            f"## {_grade_badge(thesis.grade)} {thesis.symbol} — {thesis.as_of}",
            "",
            f"- **strategy**: {thesis.strategy}",
            f"- **confidence**: {thesis.confidence}",
            f"- **market**: {thesis.market} ({thesis.currency})",
        ]
        if thesis.pattern:
            lines.append(f"- **pattern**: {thesis.pattern}")
        if getattr(thesis, "measured_move_target", None):
            lines.append(f"- **target**: {thesis.measured_move_target}")
        inv = getattr(thesis, "invalidation", None)
        if inv:
            bits = []
            if inv.stop_price is not None:
                bits.append(f"stop {inv.stop_price}")
            if inv.time_stop_days is not None:
                bits.append(f"time stop {inv.time_stop_days}d")
            if inv.rule:
                bits.append(inv.rule)
            if bits:
                lines.append(f"- **invalidation**: {', '.join(bits)}")
        if thesis.evidence:
            lines += ["", "| dimension | value | verdict |", "|---|---|---|"]
            for e in thesis.evidence:
                lines.append(f"| {e.label} | {e.value} | {e.verdict} |")
        if getattr(thesis, "narrative", ""):
            lines += ["", thesis.narrative]
        lines.append("")
        return "\n".join(lines) + "\n"


class JsonlFileDelivery:
    name = "jsonl_file"

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path).expanduser() if path else (
            Path("~/.trade-monitor/theses.jsonl").expanduser()
        )

    def deliver(self, thesis: Thesis, **options: Any) -> bool:
        import dataclasses

        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = dataclasses.asdict(thesis)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(data, default=str) + "\n")
        return True


def deliver_all(theses, adapter_name: str = "console", **options: Any) -> int:
    """Deliver a batch of theses through one adapter. Returns the count sent."""
    adapter = REGISTRY.create(adapter_name, **options)
    sent = 0
    for t in theses:
        try:
            if adapter.deliver(t):
                sent += 1
        except Exception:  # noqa: BLE001 - delivery failure must never abort a run
            continue
    return sent


REGISTRY: Registry[object] = Registry()
REGISTRY.kind = "delivery"
REGISTRY.register(
    "console", ConsoleDelivery,
    description="Print one summary line per thesis.",
    version="0.1.0",
)
REGISTRY.register(
    "markdown_file", MarkdownFileDelivery,
    description="Append a markdown report per thesis to a file.",
    version="0.1.0",
    config_schema={"path": {"type": "string"}},
)
REGISTRY.register(
    "jsonl_file", JsonlFileDelivery,
    description="Append one JSON object per thesis to a .jsonl file.",
    version="0.1.0",
    config_schema={"path": {"type": "string"}},
)

__all__ = [
    "REGISTRY", "ConsoleDelivery", "MarkdownFileDelivery", "JsonlFileDelivery",
    "deliver_all",
]
