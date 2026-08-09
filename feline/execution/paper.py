from __future__ import annotations

from uuid import uuid4

from feline.core.events import OrderRequest, OrderStatus, OrderType, OrderUpdate, PriceTick, Side
from feline.portfolio.models import Position
from .broker import Broker


class PaperBroker(Broker):
    def __init__(self, initial_cash: float = 100_000.0, slippage_bps: float = 1.0, volatility_slippage_multiplier: float = 5.0) -> None:
        self.cash = initial_cash
        self.slippage_bps = slippage_bps
        self.positions: dict[str, Position] = {}
        self.quotes: dict[str, PriceTick] = {}
        self.orders: dict[str, OrderUpdate] = {}
        self.protective: dict[str, tuple[float | None, float | None]] = {}
        self.volatility_slippage_multiplier = volatility_slippage_multiplier
        self.extreme_volatility = False

    def update_quote(self, quote: PriceTick) -> None:
        self.quotes[quote.instrument] = quote

    async def process_quote(self, quote: PriceTick) -> list[OrderUpdate]:
        self.update_quote(quote)
        results = []
        position = self.positions.get(quote.instrument)
        stop, target = self.protective.get(quote.instrument, (None, None))
        if position and position.quantity > 0 and ((stop is not None and quote.bid <= stop) or (target is not None and quote.bid >= target)):
            results.append(await self.close_position(quote.instrument))
        elif position and position.quantity < 0 and ((stop is not None and quote.ask >= stop) or (target is not None and quote.ask <= target)):
            results.append(await self.close_position(quote.instrument))
        return results

    def get_balance(self) -> float:
        return self.cash

    def get_positions(self) -> dict[str, Position]:
        return {key: Position(**vars(value)) for key, value in self.positions.items()}

    def get_quote(self, instrument: str) -> PriceTick | None:
        return self.quotes.get(instrument)

    async def submit_order(self, request: OrderRequest) -> OrderUpdate:
        order_id = str(uuid4())
        quote = self.get_quote(request.instrument)
        if request.order_type is not OrderType.MARKET:
            update = OrderUpdate(order_id=order_id,instrument=request.instrument,side=request.side,quantity=request.quantity,status=OrderStatus.REJECTED,reason="limit orders are reserved but not simulated in v0.2",correlation_id=request.id)
        elif quote is None or request.quantity <= 0:
            update = OrderUpdate(order_id=order_id, instrument=request.instrument, side=request.side, quantity=request.quantity, status=OrderStatus.REJECTED, reason="missing quote or invalid quantity", correlation_id=request.id)
        else:
            base = quote.ask if request.side is Side.BUY else quote.bid
            effective_bps = self.slippage_bps * (self.volatility_slippage_multiplier if self.extreme_volatility else 1.0)
            slip = 1 + (effective_bps / 10_000) * (1 if request.side is Side.BUY else -1)
            fill = base * slip
            signed = request.quantity if request.side is Side.BUY else -request.quantity
            position = self.positions.setdefault(request.instrument, Position(request.instrument))
            position.apply_fill(signed, fill)
            self.cash -= signed * fill
            update = OrderUpdate(order_id=order_id, instrument=request.instrument, side=request.side, quantity=request.quantity, status=OrderStatus.FILLED, fill_price=fill, correlation_id=request.id)
            if request.stop_price is not None or request.take_profit_price is not None:
                self.protective[request.instrument] = (request.stop_price, request.take_profit_price)
        self.orders[order_id] = update
        return update

    async def cancel_order(self, order_id: str) -> OrderUpdate:
        existing = self.orders[order_id]
        if existing.status is OrderStatus.FILLED:
            return existing
        update = OrderUpdate(order_id=order_id, instrument=existing.instrument, side=existing.side, quantity=existing.quantity, status=OrderStatus.CANCELLED)
        self.orders[order_id] = update
        return update

    async def close_position(self, instrument: str) -> OrderUpdate:
        position = self.positions.get(instrument)
        if position is None or position.quantity == 0:
            return OrderUpdate(order_id=str(uuid4()), instrument=instrument, side=Side.SELL, quantity=0, status=OrderStatus.REJECTED, reason="no open position")
        request = OrderRequest(instrument=instrument, side=Side.SELL if position.quantity > 0 else Side.BUY, quantity=abs(position.quantity), expected_price=self.quotes[instrument].mid)
        result = await self.submit_order(request)
        self.protective.pop(instrument, None)
        return result

    def portfolio_state(self) -> dict:
        unrealized = sum(p.unrealized(self.quotes[s].mid) for s,p in self.positions.items() if s in self.quotes)
        realized = sum(p.realized_pnl for p in self.positions.values())
        exposure = sum(abs(p.quantity * self.quotes[s].mid) for s,p in self.positions.items() if s in self.quotes)
        equity = self.cash + sum(p.market_value(self.quotes[s].mid) for s,p in self.positions.items() if s in self.quotes)
        return {"cash": self.cash, "equity": equity, "realized_pnl": realized, "unrealized_pnl": unrealized, "exposure": exposure}

    def restore(self, cash: float, positions: dict[str, Position]) -> None:
        self.cash, self.positions = cash, positions
