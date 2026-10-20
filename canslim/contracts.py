"""Core contracts.

Every pluggable concern implements one of these Protocols. The engine only
ever talks to these interfaces, so a component can be swapped or added without
touching the engine. OpenClaw is one consumer of this package, not a
dependency of it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Mapping, Protocol, Sequence, runtime_checkable

Grade = Literal["A", "B", "C", "D", "F"]
Verdict = Literal["pass", "fail", "unknown"]
Market = Literal["IN", "US"]


# --------------------------------------------------------------------------
# Normalized data shapes
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Bar:
    """One normalized OHLCV bar. All providers map into this."""

    date: str  # ISO date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class PriceSeries:
    symbol: str
    market: Market
    currency: str
    bars: list[Bar]

    def __len__(self) -> int:
        return len(self.bars)

    @property
    def last(self) -> Bar | None:
        return self.bars[-1] if self.bars else None


@dataclass(frozen=True)
class QuarterEPS:
    """One reported quarter of earnings per share."""

    period_end: str  # ISO date
    eps: float
    reported: bool = True


@dataclass(frozen=True)
class Fundamentals:
    """Normalized fundamentals. Fields are optional because coverage varies."""

    symbol: str
    quarterly_eps: list[QuarterEPS] = field(default_factory=list)
    annual_eps: list[QuarterEPS] = field(default_factory=list)
    float_shares: float | None = None
    shares_outstanding: float | None = None
    institutional_pct: float | None = None
    institutional_detail: list[dict[str, Any]] = field(default_factory=list)
    # Provenance: what the provider actually returned vs what we synthesized.
    degraded: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Evidence:
    """A single scored dimension with the numbers that produced it."""

    key: str
    label: str
    value: float | None
    threshold: float | None
    verdict: Verdict
    rationale: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Invalidation:
    """Explicit conditions that kill the thesis. Never implied."""

    stop_price: float | None = None
    rule: str = ""
    time_stop_days: int | None = None


@dataclass(frozen=True)
class Thesis:
    """The canonical record. Downstream only renders this; never recomputes."""

    id: str
    symbol: str
    name: str
    market: Market
    currency: str
    as_of: str
    strategy: str
    grade: Grade
    confidence: int  # 0-100
    evidence: list[Evidence]
    invalidation: Invalidation
    measured_move_target: float | None = None
    pattern: str | None = None
    pattern_detail: dict[str, Any] = field(default_factory=dict)
    degraded_inputs: list[str] = field(default_factory=list)
    narrative: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def passed(self) -> list[Evidence]:
        return [e for e in self.evidence if e.verdict == "pass"]

    @property
    def failed(self) -> list[Evidence]:
        return [e for e in self.evidence if e.verdict == "fail"]

    @property
    def unknown(self) -> list[Evidence]:
        return [e for e in self.evidence if e.verdict == "unknown"]


# --------------------------------------------------------------------------
# Plugin Protocols
# --------------------------------------------------------------------------


@runtime_checkable
class DataFeed(Protocol):
    """Source of market data. Implementations wrap one upstream provider."""

    name: str

    def price_series(self, symbol: str, days: int) -> PriceSeries | None: ...

    def fundamentals(self, symbol: str) -> Fundamentals | None: ...

    def index_for(self, market: Market) -> str | None:
        """Benchmark symbol used for relative strength and market direction."""
        ...


@runtime_checkable
class PatternDetector(Protocol):
    """Chart pattern detection. Adds without touching the engine."""

    name: str

    def detect(self, series: PriceSeries, **options: Any) -> dict[str, Any] | None:
        """Return pattern geometry, or None if not present."""
        ...


@runtime_checkable
class Strategy(Protocol):
    """A scoring strategy (CAN SLIM is one; others plug in alongside).

    A strategy declares which patterns it needs. The engine detects those
    patterns once per symbol and hands the results to every strategy, so two
    strategies are always compared on identical inputs.
    """

    name: str
    requires: tuple[str, ...]  # pattern names it needs
    needs_fundamentals: bool  # False lets the engine skip the expensive fetch

    def score(
        self,
        series: PriceSeries,
        fundamentals: Fundamentals,
        patterns: Mapping[str, dict[str, Any]] | None = None,
        **options: Any,
    ) -> tuple[list[Evidence], Grade, int]: ...

    def thesis(
        self,
        *,
        series: PriceSeries,
        fundamentals: Fundamentals,
        patterns: Mapping[str, dict[str, Any]] | None = None,
        evidence: Sequence[Evidence],
        grade: Grade,
        confidence: int,
        **options: Any,
    ) -> Thesis: ...

    def narrative(self, thesis: Thesis) -> str: ...


@runtime_checkable
class Validator(Protocol):
    """Independent review of a thesis.

    This is the seam where an LLM may participate. A model can challenge
    evidence and adjust confidence, but it cannot add or alter raw data, and
    a failing rule-based validator must never be overridden by a model.
    """

    name: str
    kind: Literal["rule", "model"]  # declared so provenance is auditable

    def validate(self, thesis: Thesis) -> "ValidationResult": ...


@dataclass(frozen=True)
class ValidationResult:
    validator: str
    kind: Literal["rule", "model"]
    passed: bool
    confidence_delta: int = 0  # additive, clamped by engine to 0-100
    blocking: bool = False  # if True, a False verdict rejects the thesis
    findings: list[str] = field(default_factory=list)
    model: str | None = None  # populated for kind="model" so it's auditable


@runtime_checkable
class DeliveryAdapter(Protocol):
    """Renders a thesis to a destination. Never fetches or recomputes data."""

    name: str

    def deliver(self, thesis: Thesis, **options: Any) -> bool: ...