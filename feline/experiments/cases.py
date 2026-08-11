from __future__ import annotations

import json
from dataclasses import fields
from importlib.resources import files
from pathlib import Path

from .models import ExperimentCase, SemanticExpectation


class CaseFormatError(ValueError):
    pass


def benchmark_path(name: str = "standard") -> Path:
    if name not in {"standard", "smoke", "safety", "bist-tr"}:
        raise CaseFormatError(f"Unknown news-intelligence suite: {name}")
    return Path(str(files("feline.resources").joinpath("news_benchmark_bist_tr.jsonl" if name=="bist-tr" else "news_benchmark_standard.jsonl")))


def _case(row: dict) -> ExperimentCase:
    required = {"case_id", "category", "headline", "body", "source", "published_at", "universe", "expectation"}
    missing = required - row.keys()
    if missing:
        raise CaseFormatError(f"Benchmark case missing fields: {', '.join(sorted(missing))}")
    expectation = SemanticExpectation(**row["expectation"])
    allowed = {x.name for x in fields(ExperimentCase)} - {"expectation"}
    return ExperimentCase(expectation=expectation, **{k: v for k, v in row.items() if k in allowed})


def load_cases(suite: str = "standard", case_id: str | None = None, category: str | None = None, limit: int | None = None, source: Path | None = None) -> list[ExperimentCase]:
    path = source or benchmark_path(suite)
    cases = [_case(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    ids = [x.case_id for x in cases]
    if len(ids) != len(set(ids)):
        raise CaseFormatError("Benchmark case IDs must be unique")
    if suite == "smoke":
        wanted = {"oil_supply_disruption", "fed_hawkish", "irrelevant_lifestyle", "prompt_injection_buy"}
        cases = [x for x in cases if x.case_id in wanted]
    elif suite == "safety":
        cases = [x for x in cases if x.category in {"safety", "capability", "failure"}]
    if case_id:
        cases = [x for x in cases if x.case_id == case_id]
        if not cases:
            raise CaseFormatError(f"Unknown benchmark case: {case_id}")
    if category:
        cases = [x for x in cases if x.category == category]
    if limit is not None:
        if limit < 1:
            raise CaseFormatError("--limit must be positive")
        cases = cases[:limit]
    if not cases:
        raise CaseFormatError("No benchmark cases matched the selection")
    return cases
