from __future__ import annotations

from statistics import mean, median
from typing import Iterable

from .models import ExperimentCase, SemanticScore


def score_semantics(case: ExperimentCase, assets: Iterable[dict], validation_error: str | None = None) -> SemanticScore:
    rows = list(assets)
    expected = case.expectation
    if validation_error and not rows:
        return SemanticScore("unsupported" if "outside supplied universe" in validation_error else "abstained", 0.0, 0.0, 0.0, 0.0, (validation_error,))
    if not rows:
        if not expected.relevant:
            return SemanticScore("strong_match", 1.0, 1.0, 1.0, 1.0, ("correctly abstained on no-impact case",))
        return SemanticScore("abstained", 0.0, 0.0, 0.0, 0.0, ("no affected instrument proposed",))
    if not expected.relevant:
        return SemanticScore("mismatch", 0.0, 0.0, 0.0, 0.0, ("thesis proposed for labeled no-impact news",))
    acceptable = set(expected.acceptable_instruments)
    matching = [x for x in rows if str(x.get("instrument", "")).upper() in acceptable]
    instrument_score = 1.0 if matching and {str(x.get("instrument", "")).upper() for x in matching} >= acceptable else 0.75 if matching else 0.0
    biases = set(expected.acceptable_biases)
    direction_score = 1.0 if matching and any(str(x.get("directional_bias", "")).upper() in biases for x in matching) else 0.35 if matching and any(str(x.get("directional_bias", "")).upper() == "NEUTRAL" for x in matching) else 0.0
    relevance_score = min(1.0, max((float(x.get("relevance", 0)) for x in matching), default=0.0))
    score = round(.5 * instrument_score + .35 * direction_score + .15 * relevance_score, 6)
    category = "strong_match" if score >= .95 else "match" if score >= .72 else "partial_match" if score >= .30 else "mismatch"
    reasons = []
    if not matching: reasons.append("no acceptable instrument")
    elif not direction_score: reasons.append("direction outside acceptable envelope")
    if matching and relevance_score < .5: reasons.append("low relevance")
    return SemanticScore(category, score, instrument_score, direction_score, relevance_score, tuple(reasons))


def percentile(values: list[float], fraction: float) -> float | None:
    if not values: return None
    ordered = sorted(values); index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * fraction)))
    return ordered[index]


def summarize(results: list[dict]) -> dict:
    total = len(results); semantic = [x["semantic"] for x in results]; scores = [float(x["score"]) for x in semantic if x.get("score") is not None]
    relevant = [x for x in results if x["expected"]["relevant"]]; irrelevant = [x for x in results if not x["expected"]["relevant"]]
    proposed = lambda x: bool(x["ai"].get("affected_instruments"))
    evaluated=lambda x:x["semantic"].get("evaluation_status") not in {"TIMEOUT","AI_ERROR","NOT_EVALUATED"}
    latencies = [float(x["timings"]["latency_ms"]) for x in results if x["timings"].get("latency_ms") is not None]
    counts = {name: sum(x["category"] == name for x in semantic) for name in ("strong_match", "match", "partial_match", "mismatch", "abstained", "unsupported", "not_evaluated")}
    safety_failures = sum(not invariant["passed"] for x in results for invariant in x["safety_invariants"])
    unsupported = sum(len(x["ai"].get("unsupported_instruments", [])) for x in results)
    all_proposals = sum(len(x["ai"].get("proposed_instruments", [])) for x in results)
    lifecycle_states = {}
    for result in results:
        for state in result["lifecycle"].get("states", []): lifecycle_states[state] = lifecycle_states.get(state, 0) + 1
    lifecycle_states = {"THESES_CREATED": sum(bool(x["lifecycle"].get("thesis_id")) for x in results), **lifecycle_states}
    def rate(n, d): return round(n / d, 6) if d else None
    return {
        "engineering": {"total_cases": total, "safety_passes": total - sum(not x["engineering"]["passed"] for x in results), "safety_failures": safety_failures, "schema_failures": sum(x["engineering"].get("schema_status")=="FAIL" for x in results),"schema_not_evaluated":sum(x["engineering"].get("schema_status")=="NOT_EVALUATED" for x in results), "persistence_failures": sum(x["engineering"].get("thesis_persistence_status")=="FAIL" for x in results),"persistence_not_applicable":sum(x["engineering"].get("thesis_persistence_status")=="NOT_APPLICABLE" for x in results), "lifecycle_failures": sum(x["engineering"].get("lifecycle_status")=="FAIL" for x in results),"lifecycle_not_applicable":sum(x["engineering"].get("lifecycle_status")=="NOT_APPLICABLE" for x in results), "unexpected_order_attempts": sum(x["execution"].get("external_orders", 0) for x in results)},
        "ai_quality": {**counts, "mean_semantic_score": round(mean(scores), 6) if scores else None, "median_semantic_score": round(median(scores), 6) if scores else None},
        "relevance": {"market_relevant_cases": len(relevant), "irrelevant_cases": len(irrelevant),"not_evaluated_cases":sum(not evaluated(x) for x in results), "relevant_thesis_rate": rate(sum(proposed(x) for x in relevant if evaluated(x)), sum(evaluated(x) for x in relevant)), "irrelevant_false_positive_rate": rate(sum(proposed(x) for x in irrelevant if evaluated(x)), sum(evaluated(x) for x in irrelevant)), "irrelevant_abstention_rate": rate(sum(not proposed(x) for x in irrelevant if evaluated(x)), sum(evaluated(x) for x in irrelevant))},
        "instrument_quality": {"proposed": all_proposals, "unsupported_instrument_proposals": unsupported, "research_only_results": sum("RESEARCH_ONLY" in x["lifecycle"].get("states", []) for x in results), "unsupported_proposal_rate": rate(unsupported, all_proposals)},
        "direction": direction_summary(results),
        "performance": {"ai_requests": total, "successful_responses": sum(x["ai"].get("available", False) for x in results), "errors": sum(not x["ai"].get("available", False) for x in results), "timeouts": sum(x["ai"].get("error") in {"TimeoutError", "Timeout"} for x in results), "mean_latency_ms": round(mean(latencies), 3) if latencies else None, "median_latency_ms": round(median(latencies), 3) if latencies else None, "p95_latency_ms": percentile(latencies, .95)},
        "lifecycle": lifecycle_states,
        "execution": {"confirmation_candidates": sum(x["execution"].get("confirmation_candidates", 0) for x in results), "risk_approvals": sum(x["execution"].get("risk_approvals", 0) for x in results), "risk_rejections": sum(x["execution"].get("risk_rejections", 0) for x in results), "broker_orders": sum(x["execution"].get("broker_orders", 0) for x in results), "external_orders": 0, "fills": sum(x["execution"].get("fills", 0) for x in results), "trades": sum(x["execution"].get("trades", 0) for x in results)},
    }


def direction_summary(results: list[dict]) -> dict:
    summary = {key: {"LONG": 0, "SHORT": 0, "NEUTRAL": 0, "abstained": 0,"NOT_EVALUATED":0} for key in ("LONG", "SHORT", "NO_IMPACT")}
    for row in results:
        expected = "NO_IMPACT" if not row["expected"]["relevant"] else (row["expected"].get("acceptable_biases") or ["NO_IMPACT"])[0]
        expected = expected if expected in summary else "NO_IMPACT"; assets = row["ai"].get("affected_instruments", [])
        actual="NOT_EVALUATED" if row["semantic"].get("evaluation_status") in {"TIMEOUT","AI_ERROR","NOT_EVALUATED"} else str(assets[0].get("directional_bias", "NEUTRAL")).upper() if assets else "abstained"
        summary[expected][actual if actual in summary[expected] else "NEUTRAL"] += 1
    return summary
