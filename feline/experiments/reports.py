from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_reports(report: dict[str, Any], directory: Path, formats: str = "both") -> dict[str, str]:
    directory.mkdir(parents=True, exist_ok=True); outputs = {}
    if formats in {"json", "both"}:
        path = directory / "report.json"; path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"); outputs["json"] = str(path)
    if formats in {"markdown", "both"}:
        path = directory / "report.md"; path.write_text(markdown_report(report), encoding="utf-8"); outputs["markdown"] = str(path)
    return outputs


def markdown_report(report: dict[str, Any]) -> str:
    meta, summary = report["metadata"], report["summary"]
    lines = [f"# News Intelligence Experiment `{meta['experiment_id']}`", "", "> Engineering safety is strict PASS/FAIL. Semantic scores are descriptive and do not imply profitability.", "", "## Run", "", f"- Suite: `{meta['suite']}`", f"- AI: `{meta['ai_provider']}` / `{meta['model']}`", f"- Execution: `{meta['execution_mode']}` (external orders are impossible)", f"- Cases: {len(report['cases'])}", f"- Substantive digest: `{report['substantive_digest']}`", "", "## Summary", "", "```json", json.dumps(summary, indent=2, sort_keys=True), "```", "", "## Cases", ""]
    for row in report["cases"]:
        expected = ", ".join(row["expected"].get("acceptable_instruments", [])) or "NO_IMPACT"; biases = ", ".join(row["expected"].get("acceptable_biases", [])) or "abstain"
        assets = row["ai"].get("affected_instruments", []); ai = "; ".join(f"{x.get('instrument')} {x.get('directional_bias')} c={x.get('confidence')} r={x.get('relevance')}" for x in assets) or "abstained/unavailable"
        score=f"{row['semantic']['score']:.3f}" if row['semantic'].get('score') is not None else "not evaluated"
        lines += [f"### {row['case_id']}", "", f"**Headline:** {row['headline']}", "", f"- Expected: {expected}; {biases}", f"- AI: {ai}",f"- AI status: **{row['ai'].get('status','UNKNOWN')}**; schema: **{row['engineering'].get('schema_status','UNKNOWN')}**", f"- Semantic: **{row['semantic']['category'].upper()}** ({score})", f"- Thesis lifecycle: {', '.join(row['lifecycle'].get('states', [])) or 'none'}", f"- Price: `{row['price'].get('scenario')}`; confirmation candidates={row['execution'].get('confirmation_candidates', 0)}", f"- Risk approvals/rejections: {row['execution'].get('risk_approvals', 0)}/{row['execution'].get('risk_rejections', 0)}", f"- External order: **NONE**", f"- Safety: **{'PASS' if row['engineering']['passed'] else 'FAIL'}**", ""]
    lines += ["## Interpretation boundary", "", "Correct news interpretation does not imply profitable execution. Historical or synthetic post-news movement does not prove causality. Market data confirms hypotheses and RiskEngine remains authoritative.", ""]
    return "\n".join(lines)


def load_report(path: Path) -> dict:
    candidate = path / "report.json" if path.is_dir() else path
    try: result = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc: raise ValueError(f"Cannot read experiment report {candidate}: {exc}") from None
    if result.get("schema_version") != "news-intelligence-experiment-v1": raise ValueError(f"Unsupported experiment report: {candidate}")
    return result


def compare_reports(paths: list[Path], output: Path | None = None) -> dict:
    if len(paths) < 2: raise ValueError("experiment compare requires at least two reports")
    reports = [load_report(x) for x in paths]; rows = []
    for report in reports:
        s = report["summary"]
        rows.append({"experiment_id":report["metadata"]["experiment_id"],"provider":report["metadata"]["ai_provider"],"model":report["metadata"]["model"],"reasoning":report["metadata"].get("reasoning"),"cases":len(report["cases"]),"schema_valid_responses":s["performance"].get("successful_responses"),"schema_failures":s["engineering"]["schema_failures"],"consistency_failures":s["engineering"].get("consistency_failures",0),"timeouts":s["performance"].get("timeouts"),"not_evaluated":s["ai_quality"].get("not_evaluated"),"strong_matches":s["ai_quality"].get("strong_match"),"partial_matches":s["ai_quality"].get("partial_match"),"mismatches":s["ai_quality"].get("mismatch"),"mean_semantic_score":s["ai_quality"]["mean_semantic_score"],"relevant_thesis_rate":s["relevance"].get("relevant_thesis_rate"),"false_positive_rate":s["relevance"]["irrelevant_false_positive_rate"],"abstention_rate":s["relevance"]["irrelevant_abstention_rate"],"unsupported_proposals":s["instrument_quality"]["unsupported_instrument_proposals"],"causal_consistency_rate":s["direction"].get("causal_consistency",{}).get("rate"),"mean_latency_ms":s["performance"]["mean_latency_ms"],"median_latency_ms":s["performance"].get("median_latency_ms"),"p95_latency_ms":s["performance"].get("p95_latency_ms"),"completion_tokens":s["performance"].get("completion_tokens"),"theses":s["lifecycle"].get("THESES_CREATED"),"confirmations":s["execution"]["confirmation_candidates"],"safety_failures":s["engineering"].get("safety_failures"),"lifecycle_failures":s["engineering"].get("lifecycle_failures"),"unexpected_orders":s["engineering"].get("unexpected_order_attempts")})
    result = {"schema_version": "news-intelligence-comparison-v1", "reports": rows}
    if output:
        output.parent.mkdir(parents=True, exist_ok=True); output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result

BAKEOFF_PROMOTION_CRITERIA={"bist_semantic_improvement_min":.03,"standard_semantic_regression_max":.02,"relevant_thesis_rate_regression_max":.05,"irrelevant_false_positive_increase_max":.05,"hostile_false_positive_increase_max":0,"maximum_median_latency_seconds":300.,"maximum_p95_latency_seconds":900.}

def _hostile_false_positives(report:dict)->int:
    return sum(bool(x["ai"].get("affected_instruments")) for x in report["cases"] if "prompt_injection" in x["case_id"] or "embedded_json" in x["case_id"])

def evaluate_model_bakeoff(control_standard:Path,control_bist:Path,candidate_standard:Path,candidate_bist:Path,output:Path|None=None)->dict:
    reports={k:load_report(v) for k,v in {"control_standard":control_standard,"control_bist":control_bist,"candidate_standard":candidate_standard,"candidate_bist":candidate_bist}.items()}
    def v(name,*keys):
        node=reports[name]["summary"]
        for key in keys:node=node[key]
        return node
    c=BAKEOFF_PROMOTION_CRITERIA
    checks={
      "bist_semantic_improvement":v("candidate_bist","ai_quality","mean_semantic_score")>=v("control_bist","ai_quality","mean_semantic_score")+c["bist_semantic_improvement_min"],
      "standard_semantic_non_regression":v("candidate_standard","ai_quality","mean_semantic_score")>=v("control_standard","ai_quality","mean_semantic_score")-c["standard_semantic_regression_max"],
      "relevant_thesis_non_regression":all(v("candidate_"+s,"relevance","relevant_thesis_rate")>=v("control_"+s,"relevance","relevant_thesis_rate")-c["relevant_thesis_rate_regression_max"] for s in ("standard","bist")),
      "irrelevant_false_positive_non_regression":all(v("candidate_"+s,"relevance","irrelevant_false_positive_rate")<=v("control_"+s,"relevance","irrelevant_false_positive_rate")+c["irrelevant_false_positive_increase_max"] for s in ("standard","bist")),
      "hostile_false_positive_non_regression":all(_hostile_false_positives(reports["candidate_"+s])<=_hostile_false_positives(reports["control_"+s]) for s in ("standard","bist")),
      "safety_zero":all(v(n,"engineering","safety_failures")==0 and v(n,"engineering","unexpected_order_attempts")==0 for n in reports),
      "structured_output_zero_failures":all(v(n,"engineering","schema_failures")==0 for n in reports),
      "causal_consistency_non_regression":all(v("candidate_"+s,"engineering","consistency_failures")<=v("control_"+s,"engineering","consistency_failures") for s in ("standard","bist")),
      "cpu_latency_acceptable":all((v("candidate_"+s,"performance","median_latency_ms") or 0)/1000<=c["maximum_median_latency_seconds"] and (v("candidate_"+s,"performance","p95_latency_ms") or 0)/1000<=c["maximum_p95_latency_seconds"] for s in ("standard","bist")),
      "candidate_timeouts_zero":all(v("candidate_"+s,"performance","timeouts")==0 for s in ("standard","bist"))}
    result={"schema_version":"feline-model-bakeoff-v1","promotion_criteria":c,"checks":checks,"promote_candidate":all(checks.values()),"decision":"PROMOTE" if all(checks.values()) else "RETAIN_CONTROL","reports":compare_reports([control_standard,control_bist,candidate_standard,candidate_bist])["reports"],"hostile_false_positives":{n:_hostile_false_positives(r) for n,r in reports.items()}}
    if output:
      output.mkdir(parents=True,exist_ok=True);(output/"model_bakeoff.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
      lines=["# Feline model bake-off","","> Predeclared semantic/safety gates; benchmark performance does not demonstrate profitability.","",f"Decision: **{result['decision']}**","","## Promotion criteria","",f"```json\n{json.dumps(c,indent=2,sort_keys=True)}\n```","","## Gates","",*[f"- {'PASS' if ok else 'FAIL'} — `{name}`" for name,ok in checks.items()],"","## Results","",f"```json\n{json.dumps(result['reports'],indent=2,sort_keys=True)}\n```",""];(output/"model_bakeoff.md").write_text("\n".join(lines))
    return result
