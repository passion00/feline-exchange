from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

from feline.core.events import FillEvent, Side

ACCOUNTING_VERSION = "1.1"
ACCOUNTING_TOLERANCE = 1e-12
CLASSIFICATION_TOLERANCE = 1e-12


@dataclass(frozen=True)
class TradeAccounting:
    reference_entry_price: float
    reference_exit_price: float
    reference_gross_pnl: float
    execution_pnl: float
    spread_costs: float
    slippage_costs: float
    commissions: float
    financing_costs: float
    net_pnl: float

    @property
    def gross_pnl(self) -> float:
        """Deprecated compatibility alias: actual fill-to-fill execution P&L."""
        return self.execution_pnl

    def to_dict(self) -> dict[str, float]:
        return {**asdict(self), "gross_pnl": self.gross_pnl}


def fill_mid_reference(fill: FillEvent) -> float:
    """Recover the frictionless mid that produced the bid/ask reference fill."""
    spread = float(fill.assumptions.get("spread", 0.0))
    return fill.reference_price - spread / 2 if fill.side is Side.BUY else fill.reference_price + spread / 2


def calculate_trade_accounting(entry: FillEvent, exit: FillEvent, financing_costs: float = 0.0) -> TradeAccounting:
    if entry.instrument != exit.instrument:
        raise ValueError("entry and exit instruments differ")
    if abs(entry.quantity - exit.quantity) > ACCOUNTING_TOLERANCE:
        raise ValueError(f"accounting requires matched entry/exit quantity: {entry.quantity} != {exit.quantity}")
    if entry.side is exit.side:
        raise ValueError("entry and exit sides must oppose")
    direction = 1.0 if entry.side is Side.BUY else -1.0
    quantity = entry.quantity
    reference_entry, reference_exit = fill_mid_reference(entry), fill_mid_reference(exit)
    reference_gross = (reference_exit-reference_entry)*quantity*direction
    execution_pnl = (exit.fill_price-entry.fill_price)*quantity*direction
    spread = entry.spread_cost + exit.spread_cost
    slippage = entry.slippage_amount + exit.slippage_amount
    commissions = entry.commission + exit.commission
    result = TradeAccounting(reference_entry,reference_exit,reference_gross,execution_pnl,spread,slippage,commissions,financing_costs,execution_pnl-commissions-financing_costs)
    validate_trade_accounting(result)
    return result


def validate_trade_accounting(value: TradeAccounting, tolerance: float = ACCOUNTING_TOLERANCE) -> None:
    reference_net = value.reference_gross_pnl-value.spread_costs-value.slippage_costs-value.commissions-value.financing_costs
    execution_net = value.execution_pnl-value.commissions-value.financing_costs
    scale=max(1.0,abs(value.reference_gross_pnl),abs(value.net_pnl))
    if abs(reference_net-value.net_pnl)>tolerance*scale:
        raise ValueError(f"reference accounting does not reconcile: {reference_net} != {value.net_pnl}")
    if abs(execution_net-value.net_pnl)>tolerance*scale:
        raise ValueError(f"execution accounting does not reconcile: {execution_net} != {value.net_pnl}")


def classify_net_pnl(net_pnl: float, tolerance: float = CLASSIFICATION_TOLERANCE) -> str:
    if net_pnl > tolerance:return "winner"
    if net_pnl < -tolerance:return "loser"
    return "breakeven"


def directional_excursions(entry_reference: float, side: Side, highs: Iterable[float], lows: Iterable[float]) -> tuple[float,float]:
    """Return normalized (MAE <= 0, MFE >= 0), clamped at zero by convention."""
    highs, lows = list(highs), list(lows)
    if not highs or not lows or len(highs)!=len(lows):raise ValueError("matched high/low observations required")
    if entry_reference<=0:raise ValueError("entry reference must be positive")
    if side is Side.BUY:
        favorable=max((high/entry_reference-1 for high in highs),default=0.0);adverse=min((low/entry_reference-1 for low in lows),default=0.0)
    else:
        favorable=max(((entry_reference-low)/entry_reference for low in lows),default=0.0);adverse=min(((entry_reference-high)/entry_reference for high in highs),default=0.0)
    return min(0.0,adverse),max(0.0,favorable)
