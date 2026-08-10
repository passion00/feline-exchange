from __future__ import annotations

import csv
import hashlib
import json
import math
import subprocess
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Iterable

from feline import __version__
from feline.core.events import AIAnalysisResult, FeedHealthEvent, RiskEvent, SignalEvent

VALIDATION_SCHEMA = "1.0"
VALIDATION_MODES = ("deterministic", "advisory", "confirm_or_veto")
MIN_CONCLUSIVE_TRADES = 30


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def _git_metadata() -> tuple[str | None, bool | None]:
    try:
        commit=subprocess.run(["git","rev-parse","HEAD"],capture_output=True,text=True,check=True).stdout.strip()
        dirty=bool(subprocess.run(["git","status","--porcelain","--untracked-files=no"],capture_output=True,text=True,check=True).stdout.strip())
        return commit,dirty
    except (OSError,subprocess.SubprocessError): return None,None


@dataclass
class RealtimeValidationTracker:
    mode: str
    deterministic_signals: int = 0
    final_signals: int = 0
    ai_requests: int = 0
    ai_responses: int = 0
    ai_available: int = 0
    ai_errors: Counter = field(default_factory=Counter)
    ai_decisions: Counter = field(default_factory=Counter)
    blocked_decisions: Counter = field(default_factory=Counter)
    risk_blocks: Counter = field(default_factory=Counter)
    confidence: list[float] = field(default_factory=list)
    latency_ms: list[float] = field(default_factory=list)
    stale_responses: int = 0
    feed_states: Counter = field(default_factory=Counter)

    def __post_init__(self) -> None:
        if self.mode not in VALIDATION_MODES:
            raise ValueError("invalid realtime validation mode")

    def record_ai_request(self) -> None:
        self.ai_requests += 1

    async def observe(self, event) -> None:
        if isinstance(event, SignalEvent):
            self.deterministic_signals += 1
            if self.mode != "confirm_or_veto": self.final_signals += 1
        elif isinstance(event, AIAnalysisResult):
            self.ai_responses += 1
            self.ai_available += int(event.available)
            self.confidence.append(float(event.confidence))
            if event.latency_ms is not None: self.latency_ms.append(float(event.latency_ms))
            if event.error: self.ai_errors[event.error] += 1
            decision = event.downstream_decision or "unknown"
            self.ai_decisions[decision] += 1
            if decision.startswith("CONFIRMED:"): self.final_signals += 1
            if decision.startswith("NO_TRADE:"):
                reason = decision.split(":", 1)[1]
                self.blocked_decisions[reason] += 1
                if reason == "stale_context": self.stale_responses += 1
        elif isinstance(event, RiskEvent) and not event.approved:
            self.risk_blocks[event.rule] += 1
        elif isinstance(event, FeedHealthEvent):
            self.feed_states[event.state] += 1

    def ai_metrics(self, queue_dropped: int = 0) -> dict:
        requests = self.ai_requests or self.ai_responses
        errors = sum(self.ai_errors.values()) + max(0, requests - self.ai_responses)
        vetoes = sum(v for k, v in self.ai_decisions.items() if k.startswith("NO_TRADE:"))
        approvals = sum(v for k, v in self.ai_decisions.items() if k.startswith("CONFIRMED:"))
        return {
            "requests": requests, "responses": self.ai_responses,
            "available_responses": self.ai_available, "errors": errors,
            "timeout_or_error_rate": errors / requests if requests else 0.0,
            "stale_responses": self.stale_responses,
            "stale_response_rate": self.stale_responses / self.ai_responses if self.ai_responses else 0.0,
            "approvals": approvals, "vetoes": vetoes,
            "approval_rate": approvals / self.ai_responses if self.ai_responses else 0.0,
            "veto_rate": vetoes / self.ai_responses if self.ai_responses else 0.0,
            "latency_ms": _distribution(self.latency_ms),
            "confidence": _distribution(self.confidence),
            "errors_by_type": dict(sorted(self.ai_errors.items())),
            "decisions": dict(sorted(self.ai_decisions.items())),
            "queue_dropped": int(queue_dropped),
        }


def _distribution(values: Iterable[float]) -> dict:
    values = sorted(float(v) for v in values if math.isfinite(float(v)))
    if not values: return {"n": 0, "mean": None, "median": None, "p95": None, "minimum": None, "maximum": None}
    p95 = values[min(len(values) - 1, math.ceil(.95 * len(values)) - 1)]
    return {"n": len(values), "mean": mean(values), "median": median(values), "p95": p95, "minimum": values[0], "maximum": values[-1]}


def build_validation_summary(runtime, session: dict, tracker: RealtimeValidationTracker) -> dict:
    portfolio = runtime.broker.portfolio_state()
    completed = list(runtime.trades.completed)
    pnls = [float(t.net_pnl) for t in completed]
    wins = [v for v in pnls if v > 1e-12]; losses = [v for v in pnls if v < -1e-12]
    peak = runtime.config.paper.initial_cash; max_dd = 0.0
    for equity in runtime.equity_history:
        peak = max(peak, equity); max_dd = max(max_dd, peak - equity)
    ai = tracker.ai_metrics(runtime.ai.dropped)
    duration = None
    if session.get("started_at") and session.get("ended_at"):
        duration = (datetime.fromisoformat(session["ended_at"]) - datetime.fromisoformat(session["started_at"])).total_seconds()
    costs = {key: float(portfolio.get(key, 0) or 0) for key in ("commission_costs", "spread_costs", "slippage_costs", "financing_costs")}
    warnings = []
    if runtime.tick_count == 0: warnings.append("no_market_quotes")
    if tracker.deterministic_signals == 0: warnings.append("no_deterministic_signals")
    if tracker.mode != "deterministic" and ai["requests"] == 0: warnings.append("no_ai_assessments")
    if len(completed) < MIN_CONCLUSIVE_TRADES: warnings.append(f"insufficient_trades_for_inference:{len(completed)}<{MIN_CONCLUSIVE_TRADES}")
    if completed and len(completed) < len(runtime.broker.fills) // 2: warnings.append("trade_lifecycle_coverage_incomplete")
    if tracker.feed_states.get("DEGRADED") or tracker.feed_states.get("STALE"): warnings.append("feed_degradation_observed")
    failed_feed = runtime.tick_count == 0
    status = "FAIL" if failed_feed else "WARN" if warnings else "PASS"
    mode_config = {"validation_mode": tracker.mode, "ai": asdict(runtime.config.ai), "strategy": asdict(runtime.config.strategy), "risk": asdict(runtime.config.risk), "paper": asdict(runtime.config.paper)}
    commit,dirty=_git_metadata()
    return {
        "schema_version": VALIDATION_SCHEMA, "feline_version": __version__,
        "git_commit":commit,"repository_dirty":dirty,
        "validation_session_id": session["realtime_session_id"], "realtime_session_id": session["realtime_session_id"],
        "validation_mode": tracker.mode, "provider": session["provider"], "instruments": session["instruments"],
        "started_at": session["started_at"], "ended_at": session.get("ended_at"), "duration_seconds": duration,
        "created_at": datetime.now(timezone.utc).isoformat(), "paper_only": True,
        "configuration": mode_config, "configuration_checksum": _hash(mode_config),
        "market": {"quotes": runtime.tick_count, "first_source_timestamp": session.get("first_source_timestamp"), "last_source_timestamp": session.get("last_source_timestamp"), "feed_states": dict(sorted(tracker.feed_states.items()))},
        "signals": {"deterministic_before_ai": tracker.deterministic_signals, "final_after_ai": tracker.final_signals, "blocked": sum(tracker.blocked_decisions.values()), "blocked_reasons": dict(sorted(tracker.blocked_decisions.items()))},
        "ai": ai, "risk": {"blocked": sum(tracker.risk_blocks.values()), "blocked_reasons": dict(sorted(tracker.risk_blocks.items()))},
        "execution": {"orders": len(runtime.broker.orders), "fills": len(runtime.broker.fills), "costs": costs, "total_costs": sum(costs.values())},
        "performance": {"starting_equity": runtime.config.paper.initial_cash, "ending_equity": portfolio["equity"], "net_pnl": portfolio["equity"] - runtime.config.paper.initial_cash, "realized_pnl": portfolio["realized_pnl"], "trades": len(completed), "open_trades": len(runtime.trades.open), "winners": len(wins), "losers": len(losses), "win_rate": len(wins) / len(pnls) if pnls else None, "profit_factor": sum(wins) / -sum(losses) if losses else None, "max_drawdown": max_dd, "max_drawdown_fraction": max_dd / runtime.config.paper.initial_cash if runtime.config.paper.initial_cash else None, "average_mae": mean([t.mae for t in completed]) if completed else None, "average_mfe": mean([t.mfe for t in completed]) if completed else None, "exposure_time_fraction": runtime.exposure_samples / runtime.tick_count if runtime.tick_count else 0.0},
        "validation_status": status, "warnings": warnings,
        "interpretation": "insufficient evidence" if len(completed) < MIN_CONCLUSIVE_TRADES else "descriptive session metrics only; no profitability or AI-value claim",
    }


def export_validation_summary(summary: dict, output_root: Path) -> Path:
    directory = output_root / summary["validation_session_id"]; directory.mkdir(parents=True, exist_ok=True)
    (directory / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n")
    flat = _comparison_row(summary)
    with (directory / "summary.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(flat)); writer.writeheader(); writer.writerow(flat)
    (directory / "summary.md").write_text(_summary_markdown(summary))
    return directory


def load_validation_summary(path: Path) -> dict:
    candidate = path / "summary.json" if path.is_dir() else path
    value = json.loads(candidate.read_text())
    if value.get("schema_version") != VALIDATION_SCHEMA: raise ValueError("unsupported realtime validation summary")
    return value


def compare_validation_sessions(paths: Iterable[Path], output: Path | None = None) -> dict:
    summaries = [load_validation_summary(Path(path)) for path in paths]
    modes = {s["validation_mode"] for s in summaries}
    periods = {(s["market"].get("first_source_timestamp") or s["started_at"], s["market"].get("last_source_timestamp") or s["ended_at"]) for s in summaries}
    instruments = {tuple(s["instruments"]) for s in summaries}
    equivalent = len(periods) == 1 and len(instruments) == 1
    warnings = []
    if len(modes) < 2: warnings.append("comparison_requires_at_least_two_validation_modes")
    if not equivalent: warnings.append("market_periods_or_instruments_are_not_equivalent")
    if any(s["performance"]["trades"] < MIN_CONCLUSIVE_TRADES for s in summaries): warnings.append("insufficient_trading_activity")
    conclusive = len(modes) >= 2 and equivalent and not any("insufficient" in w for w in warnings)
    result = {"schema_version": VALIDATION_SCHEMA, "comparison_basis": "exact_market_period", "equivalent_market_periods": equivalent, "configuration_differences": _config_differences(summaries), "sessions": [_comparison_row(s) for s in summaries], "validation_status": "PASS" if conclusive else "WARN", "warnings": warnings, "ai_effect": "descriptively_helping" if conclusive and _ai_delta(summaries) > 0 else "descriptively_hurting" if conclusive and _ai_delta(summaries) < 0 else "no_detectable_difference" if conclusive else "insufficient_evidence"}
    if output:
        output.mkdir(parents=True, exist_ok=True)
        (output / "comparison.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        rows = result["sessions"]
        with (output / "comparison.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else []); writer.writeheader(); writer.writerows(rows)
        (output / "comparison.md").write_text(_comparison_markdown(result))
    return result


def _ai_delta(summaries: list[dict]) -> float:
    base = next((s for s in summaries if s["validation_mode"] == "deterministic"), None)
    assisted = next((s for s in summaries if s["validation_mode"] == "confirm_or_veto"), None)
    return (assisted["performance"]["net_pnl"] - base["performance"]["net_pnl"]) if base and assisted else 0.0


def _config_differences(summaries: list[dict]) -> dict:
    if not summaries: return {}
    keys = ("ai", "strategy", "risk", "paper")
    return {key: {s["validation_session_id"]: s["configuration"][key] for s in summaries} for key in keys if len({_hash(s["configuration"][key]) for s in summaries}) > 1}


def _comparison_row(s: dict) -> dict:
    return {"session_id": s["validation_session_id"], "mode": s["validation_mode"], "started_at": s["started_at"], "ended_at": s["ended_at"], "quotes": s["market"]["quotes"], "deterministic_signals": s["signals"]["deterministic_before_ai"], "final_signals": s["signals"]["final_after_ai"], "ai_requests": s["ai"]["requests"], "ai_error_rate": s["ai"]["timeout_or_error_rate"], "ai_stale_rate": s["ai"]["stale_response_rate"], "ai_approval_rate": s["ai"]["approval_rate"], "ai_veto_rate": s["ai"]["veto_rate"], "orders": s["execution"]["orders"], "fills": s["execution"]["fills"], "trades": s["performance"]["trades"], "net_pnl": s["performance"]["net_pnl"], "win_rate": s["performance"]["win_rate"], "max_drawdown": s["performance"]["max_drawdown"], "status": s["validation_status"]}


def _summary_markdown(s: dict) -> str:
    return f"# Realtime validation {s['validation_session_id']}\n\nMode: `{s['validation_mode']}`  \nStatus: **{s['validation_status']}**  \nPeriod: {s['started_at']} to {s['ended_at']}  \nPaper only: yes\n\n- Quotes: {s['market']['quotes']}\n- Deterministic signals: {s['signals']['deterministic_before_ai']}\n- Final signals: {s['signals']['final_after_ai']}\n- AI requests / approval / veto: {s['ai']['requests']} / {s['ai']['approval_rate']:.3f} / {s['ai']['veto_rate']:.3f}\n- Trades / net P&L / drawdown: {s['performance']['trades']} / {s['performance']['net_pnl']:.6f} / {s['performance']['max_drawdown']:.6f}\n- Warnings: {', '.join(s['warnings']) or 'none'}\n\nThese descriptive paper-session results do not establish profitability or AI value.\n"


def _comparison_markdown(r: dict) -> str:
    lines = ["# Realtime validation comparison", "", f"Status: **{r['validation_status']}**", f"AI effect: **{r['ai_effect']}**", f"Equivalent exact market periods: {r['equivalent_market_periods']}", "", "| Mode | Signals | Final | AI requests | Trades | Net P&L | Status |", "|---|---:|---:|---:|---:|---:|---|"]
    for row in r["sessions"]: lines.append(f"| {row['mode']} | {row['deterministic_signals']} | {row['final_signals']} | {row['ai_requests']} | {row['trades']} | {row['net_pnl']:.6f} | {row['status']} |")
    lines += ["", "Warnings: " + (", ".join(r["warnings"]) or "none"), "", "Comparisons are descriptive and require equivalent market periods and adequate activity. No prompt, strategy, or risk tuning is performed."]
    return "\n".join(lines) + "\n"
