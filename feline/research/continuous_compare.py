"""Read-only multi-market continuous experiment comparison."""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable


def _load(path: Path) -> tuple[Path, dict[str, Any], dict[str, Any], set[str]]:
    directory = path if path.is_dir() else path.parent
    if (directory / "INVALID.json").exists() or "invalid" in directory.parts:
        raise ValueError(f"invalidated experiment cannot be compared: {directory}")
    experiment = json.loads((directory / "experiment.json").read_text())
    summary = json.loads((directory / "summary.json").read_text())
    timestamps: set[str] = set()
    observations = directory / "observations.csv"
    if observations.exists():
        with observations.open(newline="", encoding="utf-8") as handle:
            timestamps = {row["timestamp"] for row in csv.DictReader(handle)}
    return directory, experiment, summary, timestamps


def compare_continuous_experiments(paths: Iterable[Path], output_root: Path = Path("data/reports/continuous/comparisons"), basis: str = "native") -> dict[str, Any]:
    loaded = [_load(Path(path)) for path in paths]
    if len(loaded) < 2:
        raise ValueError("continuous compare requires at least two valid experiments")
    if basis not in {"native", "common"}:
        raise ValueError("comparison basis must be native or common")
    overlap = set.intersection(*(item[3] for item in loaded)) if basis == "common" else set()
    rows = []
    for directory, experiment, summary, timestamps in loaded:
        portfolio = summary.get("portfolio", {})
        start = experiment.get("start_timestamp"); end = experiment.get("end_timestamp")
        days = max(1 / 1440, (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() / 86400) if start and end else 0
        rows.append({"experiment_id": experiment.get("experiment_id", directory.name), "instrument": experiment.get("instrument"),
                     "start": start, "end": end, "bars": summary.get("bars"), "comparison_bars": len(overlap) if basis == "common" else len(timestamps),
                     "market_profile": experiment.get("market_profile", {}).get("profile_version", "legacy"),
                     "execution_profile": experiment.get("execution_profile", {}).get("profile_name", "legacy"),
                     "calibrated": experiment.get("execution_profile", {}).get("calibrated"), "trades": summary.get("trades", 0),
                     "trades_per_day": summary.get("trades", 0) / days if days else None,
                     "total_R": portfolio.get("total_R"), "expectancy_R": portfolio.get("expectancy_R"),
                     "profit_factor": _portfolio_profit_factor(summary), "win_rate": _portfolio_win_rate(summary),
                     "realized_max_drawdown_percent": portfolio.get("realized_max_drawdown_percent"),
                     "realized_max_drawdown_R": portfolio.get("realized_max_drawdown_R"),
                     "reference_gross_pnl": portfolio.get("reference_gross_pnl"), "execution_costs": portfolio.get("total_execution_cost", portfolio.get("transaction_costs")),
                     "net_pnl": portfolio.get("net_pnl"), "edge_cost_ratio": portfolio.get("reference_edge_to_cost_ratio"),
                     "break_even_cost": portfolio.get("break_even_average_cost_per_trade"), "regime_counts": summary.get("regime_counts", {}),
                     "strategies": summary.get("strategies", {})})
    identity = sha256(json.dumps({"experiments":[row["experiment_id"] for row in rows],"basis":basis}, sort_keys=True).encode()).hexdigest()[:20]
    output = output_root / identity; output.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version":"1.0", "comparison_id":identity, "basis":basis, "common_timestamp_count":len(overlap) if basis=="common" else None,
               "warning":"Descriptive development research; nominal P/L is not a fair market ranking.", "markets":rows}
    (output / "comparison.json").write_text(json.dumps(payload, indent=2) + "\n")
    flat = [{key:(json.dumps(value,sort_keys=True) if isinstance(value,dict) else value) for key,value in row.items()} for row in rows]
    with (output / "comparison.csv").open("w", newline="", encoding="utf-8") as handle:
        writer=csv.DictWriter(handle,fieldnames=list(flat[0]));writer.writeheader();writer.writerows(flat)
    lines=["# Continuous multi-market comparison", "", f"Basis: **{basis}**", "", "Nominal P/L is not used to rank markets. All results are descriptive development research.", "",
           "| Market | Trades | Total R | Expectancy R | Max DD % | Net P/L | Edge/cost |", "|---|---:|---:|---:|---:|---:|---:|"]
    for row in rows: lines.append(f"| {row['instrument']} | {row['trades']} | {row['total_R']} | {row['expectancy_R']} | {row['realized_max_drawdown_percent']} | {row['net_pnl']} | {row['edge_cost_ratio']} |")
    (output / "comparison.md").write_text("\n".join(lines) + "\n")
    return {"comparison_id":identity,"output_directory":str(output),**payload}


def _portfolio_profit_factor(summary: dict[str, Any]) -> float | None:
    strategies=summary.get("strategies",{});wins=sum(float(row.get("average_win") or 0)*int(row.get("winners",0)) for row in strategies.values());losses=-sum(float(row.get("average_loss") or 0)*int(row.get("losers",0)) for row in strategies.values())
    return wins/losses if losses else None


def _portfolio_win_rate(summary: dict[str, Any]) -> float | None:
    strategies=summary.get("strategies",{});wins=sum(int(row.get("winners",0)) for row in strategies.values());trades=sum(int(row.get("executed_trades",0)) for row in strategies.values())
    return wins/trades if trades else None
