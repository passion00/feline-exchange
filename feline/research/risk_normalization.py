"""Cross-market sizing and descriptive portfolio analytics.

This module has no strategy authority.  It converts an already approved setup
into auditable research sizing and summarizes corrected trade accounting.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import mean, median
from typing import Any, Iterable


@dataclass(frozen=True)
class RiskSizingConfig:
    mode: str = "risk"
    starting_equity: float = 100_000.0
    risk_fraction: float = 0.0025
    minimum_quantity: float | None = None
    maximum_quantity: float | None = None
    maximum_notional: float | None = None
    version: str = "1.0"


@dataclass(frozen=True)
class PositionSizing:
    accepted: bool
    reason: str
    quantity: float
    risk_budget: float
    initial_risk_amount: float
    stop_distance: float
    contract_multiplier: float
    notional: float
    capped: bool = False


def size_position(*, equity: float, risk_fraction: float, entry_price: float,
                  stop_price: float, contract_multiplier: float = 1.0,
                  minimum_quantity: float = 0.0, maximum_quantity: float | None = None,
                  maximum_notional: float | None = None) -> PositionSizing:
    stop = abs(entry_price - stop_price)
    budget = equity * risk_fraction
    if min(equity, risk_fraction, entry_price, contract_multiplier, stop) <= 0:
        return PositionSizing(False, "invalid_or_zero_stop_distance", 0.0, max(0.0, budget), 0.0, stop, contract_multiplier, 0.0)
    requested = budget / (stop * contract_multiplier)
    cap = requested
    if maximum_quantity is not None:
        cap = min(cap, maximum_quantity)
    if maximum_notional is not None:
        cap = min(cap, maximum_notional / (entry_price * contract_multiplier))
    if cap < minimum_quantity:
        return PositionSizing(False, "quantity_below_minimum", 0.0, budget, 0.0, stop, contract_multiplier, 0.0)
    notional = cap * entry_price * contract_multiplier
    return PositionSizing(True, "sized" if cap == requested else "sized_at_exposure_cap", cap, budget,
                          cap * stop * contract_multiplier, stop, contract_multiplier, notional, cap < requested)


def trade_r_multiple(net_pnl: float, initial_risk_amount: float) -> float | None:
    return net_pnl / initial_risk_amount if initial_risk_amount > 0 else None


def build_equity_curve(trades: Iterable[dict[str, Any]], starting_equity: float) -> tuple[list[dict[str, Any]], dict[str, float]]:
    equity = peak = starting_equity
    cumulative_r = 0.0
    worst_amount = worst_percent = worst_r = 0.0
    rows: list[dict[str, Any]] = []
    for trade in sorted(trades, key=lambda row: (row.get("exit_timestamp", ""), row.get("trade_id", ""))):
        equity += float(trade["net_pnl"])
        value_r = trade.get("pnl_R")
        if value_r is not None:
            cumulative_r += float(value_r)
        peak = max(peak, equity)
        drawdown = equity - peak
        drawdown_percent = drawdown / peak if peak else 0.0
        drawdown_r = cumulative_r - max([0.0] + [float(row["cumulative_R"]) for row in rows])
        worst_amount = min(worst_amount, drawdown)
        worst_percent = min(worst_percent, drawdown_percent)
        worst_r = min(worst_r, drawdown_r)
        rows.append({"timestamp": trade.get("exit_timestamp"), "realized_equity": equity,
                     "cumulative_net_pnl": equity - starting_equity, "cumulative_R": cumulative_r,
                     "drawdown_amount": drawdown, "drawdown_percent": drawdown_percent,
                     "drawdown_R": drawdown_r})
    return rows, {"ending_equity": equity, "realized_max_drawdown": worst_amount,
                  "realized_max_drawdown_percent": worst_percent, "realized_max_drawdown_R": worst_r}


def cost_edge_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(trades)
    reference = sum(float(row.get("reference_gross_pnl", 0)) for row in trades)
    execution = sum(float(row.get("execution_pnl", 0)) for row in trades)
    spread = sum(float(row.get("spread_costs", 0)) for row in trades)
    slip = sum(float(row.get("slippage_costs", 0)) for row in trades)
    commissions = sum(float(row.get("commissions", 0)) for row in trades)
    financing = sum(float(row.get("financing_costs", 0)) for row in trades)
    costs = spread + slip + commissions + financing
    net = sum(float(row.get("net_pnl", 0)) for row in trades)
    entry_notional = sum(abs(float(row.get("reference_entry_price", row.get("entry_price", 0))) * float(row.get("quantity", 0))) for row in trades)
    r_values = [float(row["pnl_R"]) for row in trades if row.get("pnl_R") is not None]
    wins = [value for value in r_values if value > 1e-12]
    losses = [value for value in r_values if value < -1e-12]
    mae_r = [float(row["mae_R"]) for row in trades if row.get("mae_R") is not None]
    mfe_r = [float(row["mfe_R"]) for row in trades if row.get("mfe_R") is not None]
    break_even = reference / count if count and reference > 0 else None
    return {
        "average_reference_gross_pnl_per_trade": reference / count if count else None,
        "average_execution_pnl_per_trade": execution / count if count else None,
        "average_spread_cost_per_trade": spread / count if count else None,
        "average_slippage_cost_per_trade": slip / count if count else None,
        "average_total_cost_per_trade": costs / count if count else None,
        "reference_edge_to_cost_ratio": reference / costs if costs else None,
        "total_reference_gross_pnl": reference, "total_execution_cost": costs, "total_net_pnl": net,
        "break_even_average_cost_per_trade": break_even,
        "break_even_average_cost_bps": break_even / (entry_notional / count) * 10_000 if break_even is not None and entry_notional else None,
        "break_even_average_cost_R": mean([break_even / float(row["initial_risk_amount"]) for row in trades if row.get("initial_risk_amount")]) if break_even is not None and any(row.get("initial_risk_amount") for row in trades) else None,
        "total_R": sum(r_values), "average_R_per_trade": mean(r_values) if r_values else None,
        "median_R_per_trade": median(r_values) if r_values else None,
        "average_win_R": mean(wins) if wins else None, "average_loss_R": mean(losses) if losses else None,
        "expectancy_R": mean(r_values) if r_values else None,
        "average_MAE_R": mean(mae_r) if mae_r else None, "average_MFE_R": mean(mfe_r) if mfe_r else None,
    }


def cost_sensitivity(trades: list[dict[str, Any]], multipliers: tuple[float, ...] = (0, .25, .5, .75, 1, 1.5, 2)) -> list[dict[str, float]]:
    reference = sum(float(row.get("reference_gross_pnl", 0)) for row in trades)
    costs = sum(float(row.get("spread_costs", 0)) + float(row.get("slippage_costs", 0)) + float(row.get("commissions", 0)) + float(row.get("financing_costs", 0)) for row in trades)
    return [{"cost_multiplier": value, "hypothetical_net_pnl": reference - costs * value} for value in multipliers]
