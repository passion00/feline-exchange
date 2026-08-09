from __future__ import annotations

from dataclasses import dataclass

from feline.config import RiskConfig
from feline.core.events import OrderRequest, PriceTick, RiskEvent
from feline.portfolio.models import Position


@dataclass(frozen=True)
class RiskState:
    daily_pnl: float = 0.0
    peak_equity: float = 0.0
    equity: float = 0.0


class RiskEngine:
    """Level 1 deterministic gate. Nothing can bypass approve_order()."""

    def __init__(self, config: RiskConfig) -> None:
        self.config = config
        self.kill_switch = False
        self.trading_enabled = config.trading_enabled
        self.state = RiskState()

    def activate_kill_switch(self) -> None:
        self.kill_switch = True
        self.trading_enabled = False

    def reset_kill_switch(self) -> None:
        self.kill_switch = False
        self.trading_enabled = self.config.trading_enabled

    def update_account(self, *, daily_pnl: float, equity: float, peak_equity: float) -> None:
        self.state = RiskState(daily_pnl, peak_equity, equity)
        drawdown = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0
        if daily_pnl <= -self.config.max_daily_loss or drawdown >= self.config.max_drawdown:
            self.activate_kill_switch()

    def emergency_volatility(self, volatility: float) -> bool:
        if volatility >= self.config.emergency_volatility_threshold:
            self.activate_kill_switch()
            return True
        return False

    def approve_order(self, request: OrderRequest, quote: PriceTick | None, positions: dict[str, Position]) -> RiskEvent:
        def reject(rule: str, message: str, severity: str = "warning") -> RiskEvent:
            return RiskEvent(approved=False, rule=rule, message=message, severity=severity, order_request_id=request.id, correlation_id=request.correlation_id)
        if self.kill_switch:
            return reject("kill_switch", "Global kill switch is active", "emergency")
        if not self.trading_enabled:
            return reject("trading_disabled", "New trades are disabled")
        if quote is None:
            return reject("quote", "No current quote")
        if quote.spread_ratio > self.config.max_allowed_spread:
            return reject("spread", "Spread exceeds configured maximum")
        signed = request.quantity if request.side.value == "buy" else -request.quantity
        resulting = positions.get(request.instrument, Position(request.instrument)).quantity + signed
        if abs(resulting) > self.config.max_position_size:
            return reject("position_size", "Resulting position exceeds maximum")
        exposure = sum(abs(p.quantity * (quote.mid if p.instrument == request.instrument else p.average_price)) for p in positions.values())
        exposure += abs(signed * quote.mid)
        if exposure > self.config.max_total_exposure:
            return reject("total_exposure", "Total exposure exceeds maximum")
        if request.stop_price is None:
            return reject("stop_required", "Every new order requires a stop price")
        estimated_loss = abs(request.expected_price - request.stop_price) * request.quantity
        if estimated_loss > self.config.max_loss_per_trade:
            return reject("loss_per_trade", "Estimated trade loss exceeds maximum")
        return RiskEvent(approved=True, rule="approved", message="All deterministic risk checks passed", order_request_id=request.id, correlation_id=request.correlation_id)

