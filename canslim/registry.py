"""Plugin registry.

Every pluggable concern in the system -- data feeds, strategies, patterns,
validators, delivery adapters -- registers here. Config selects from the
registry by name; nothing hardcodes a concrete implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Generic, Iterable, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class PluginSpec(Generic[T]):
    name: str
    kind: str
    factory: Callable[..., T]
    description: str = ""
    version: str = "0.0.0"
    requires: tuple[str, ...] = ()
    provides: tuple[str, ...] = ()
    config_schema: dict[str, Any] = field(default_factory=dict)


class Registry(Generic[T]):
    """Name -> factory map for one plugin kind."""

    kind = "plugin"

    def __init__(self) -> None:
        self._specs: dict[str, PluginSpec[T]] = {}

    def register(
        self,
        name: str,
        factory: Callable[..., T],
        *,
        description: str = "",
        version: str = "0.0.0",
        requires: Iterable[str] = (),
        provides: Iterable[str] = (),
        config_schema: dict[str, Any] | None = None,
    ) -> None:
        if name in self._specs:
            raise ValueError(f"{self.kind} already registered: {name}")
        self._specs[name] = PluginSpec(
            name=name,
            kind=self.kind,
            factory=factory,
            description=description,
            version=version,
            requires=tuple(requires),
            provides=tuple(provides),
            config_schema=config_schema or {},
        )

    def get(self, name: str) -> PluginSpec[T]:
        try:
            return self._specs[name]
        except KeyError:
            raise KeyError(
                f"unknown {self.kind} '{name}'. available: {sorted(self._specs)}"
            ) from None

    def create(self, name: str, **options: Any) -> T:
        spec = self.get(name)
        return spec.factory(**options)

    def names(self) -> list[str]:
        return sorted(self._specs)

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "name": s.name,
                "kind": s.kind,
                "version": s.version,
                "description": s.description,
                "requires": list(s.requires),
                "provides": list(s.provides),
                "config_schema": s.config_schema,
            }
            for s in sorted(self._specs.values(), key=lambda s: s.name)
        ]

    def __contains__(self, name: object) -> bool:
        return name in self._specs

    def __len__(self) -> int:
        return len(self._specs)