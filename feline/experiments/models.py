from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SemanticExpectation:
    relevant: bool
    acceptable_instruments: tuple[str, ...] = ()
    acceptable_biases: tuple[str, ...] = ()
    acceptable_asset_classes: tuple[str, ...] = ()
    candidate_expected: bool | None = None


@dataclass(frozen=True)
class ExperimentCase:
    case_id: str
    category: str
    headline: str
    body: str
    source: str
    published_at: str
    universe: tuple[dict[str, Any], ...]
    expectation: SemanticExpectation
    fixture_response: dict[str, Any] | None = None
    fixture_analysis: dict[str, Any] | None = None
    failure_mode: str | None = None
    price_scenario: str = "none"
    safety_expectations: tuple[str, ...] = ()
    semantic_notes: str = ""
    duplicate_of: str | None = None


@dataclass(frozen=True)
class SemanticScore:
    category: str
    score: float
    instrument_score: float
    direction_score: float
    relevance_score: float
    reasons: tuple[str, ...] = ()


@dataclass
class ExperimentResult:
    case_id: str
    category: str
    headline: str
    expected: dict[str, Any]
    ai: dict[str, Any]
    semantic: dict[str, Any]
    engineering: dict[str, Any]
    lifecycle: dict[str, Any]
    price: dict[str, Any]
    execution: dict[str, Any]
    timings: dict[str, Any]
    safety_invariants: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)
