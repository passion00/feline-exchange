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
        lines += [f"### {row['case_id']}", "", f"**Headline:** {row['headline']}", "", f"- Expected: {expected}; {biases}", f"- AI: {ai}", f"- Semantic: **{row['semantic']['category'].upper()}** ({row['semantic']['score']:.3f})", f"- Thesis lifecycle: {', '.join(row['lifecycle'].get('states', [])) or 'none'}", f"- Price: `{row['price'].get('scenario')}`; confirmation candidates={row['execution'].get('confirmation_candidates', 0)}", f"- Risk approvals/rejections: {row['execution'].get('risk_approvals', 0)}/{row['execution'].get('risk_rejections', 0)}", f"- External order: **NONE**", f"- Safety: **{'PASS' if row['engineering']['passed'] else 'FAIL'}**", ""]
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
        rows.append({"experiment_id": report["metadata"]["experiment_id"], "provider": report["metadata"]["ai_provider"], "model": report["metadata"]["model"], "cases": len(report["cases"]), "mean_semantic_score": s["ai_quality"]["mean_semantic_score"], "false_positive_rate": s["relevance"]["irrelevant_false_positive_rate"], "abstention_rate": s["relevance"]["irrelevant_abstention_rate"], "unsupported_proposals": s["instrument_quality"]["unsupported_instrument_proposals"], "schema_failures": s["engineering"]["schema_failures"], "mean_latency_ms": s["performance"]["mean_latency_ms"], "theses": s["instrument_quality"]["proposed"], "confirmations": s["execution"]["confirmation_candidates"]})
    result = {"schema_version": "news-intelligence-comparison-v1", "reports": rows}
    if output:
        output.parent.mkdir(parents=True, exist_ok=True); output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result
