"""Rule validators (VAL-1).

A validator is independent review of a thesis. These are deterministic rules,
not opinions: each checks something that is objectively true or false about the
thesis it is handed. A model validator (VAL-2) may later sit alongside them, but
the engine guarantees a model can never overturn a failing blocking rule.

Each validator implements the `Validator` protocol:

    name:  str
    kind = "rule"
    validate(thesis) -> ValidationResult
"""

from __future__ import annotations

from trade_monitor_contracts import Thesis, ValidationResult

from ..registry import Registry


class NoDegradedEvidence:
    """Reject a thesis whose inputs were degraded in a market-critical way.

    A thesis built on missing data is not a thesis -- it is a guess wearing a
    grade. This validator blocks when the thesis declares degraded inputs.
    """

    name = "no_degraded_evidence"
    kind = "rule"

    def __init__(self, *, blocking: bool = True) -> None:
        self.blocking = blocking

    def validate(self, thesis: Thesis) -> ValidationResult:
        bad = list(getattr(thesis, "degraded_inputs", []) or [])
        return ValidationResult(
            validator=self.name,
            kind=self.kind,
            passed=not bad,
            blocking=bool(self.blocking),
            confidence_delta=0,
            findings=[f"degraded input: {d}" for d in bad] or ["all inputs present"],
        )


class HasInvalidation:
    """Reject a thesis with no explicit invalidation.

    The contract says invalidation is never implied. A thesis you cannot be
    wrong about is not investable, so this blocks when none is stated.
    """

    name = "has_invalidation"
    kind = "rule"

    def __init__(self, *, blocking: bool = False) -> None:
        self.blocking = blocking

    def validate(self, thesis: Thesis) -> ValidationResult:
        inv = getattr(thesis, "invalidation", None)
        has = bool(
            inv and (inv.stop_price is not None or inv.rule or inv.time_stop_days)
        )
        return ValidationResult(
            validator=self.name,
            kind=self.kind,
            passed=has,
            blocking=bool(self.blocking),
            findings=["invalidation stated" if has else "no invalidation stated"],
        )


class EvidenceCompleteness:
    """A thesis should cite evidence. Fewer than `min_evidence` is a smell."""

    name = "evidence_completeness"
    kind = "rule"

    def __init__(self, *, min_evidence: int = 3, blocking: bool = False) -> None:
        self.min_evidence = min_evidence
        self.blocking = blocking

    def validate(self, thesis: Thesis) -> ValidationResult:
        n = len(getattr(thesis, "evidence", []) or [])
        return ValidationResult(
            validator=self.name,
            kind=self.kind,
            passed=n >= self.min_evidence,
            blocking=bool(self.blocking),
            findings=[f"{n} evidence items vs min {self.min_evidence}"],
        )


REGISTRY: Registry[object] = Registry()
REGISTRY.kind = "validator"
REGISTRY.register(
    "no_degraded_evidence",
    NoDegradedEvidence,
    description="Blocks a thesis whose inputs were declared degraded.",
    version="0.1.0",
    config_schema={"blocking": {"type": "boolean", "default": True}},
)
REGISTRY.register(
    "has_invalidation",
    HasInvalidation,
    description="Flags a thesis that states no invalidation conditions.",
    version="0.1.0",
    config_schema={"blocking": {"type": "boolean", "default": False}},
)
REGISTRY.register(
    "evidence_completeness",
    EvidenceCompleteness,
    description="Flags a thesis citing fewer than N evidence items.",
    version="0.1.0",
    config_schema={"min_evidence": {"type": "integer", "default": 3}},
)

__all__ = [
    "REGISTRY", "NoDegradedEvidence", "HasInvalidation", "EvidenceCompleteness",
]
