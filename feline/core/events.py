from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(str, Enum):
    PENDING = "pending"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    TRIGGERED = "triggered"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


class Regime(str, Enum):
    INSUFFICIENT_DATA = "insufficient_data"
    NORMAL = "normal"
    TRENDING = "trending"
    HIGH_VOLATILITY = "high_volatility"
    EXTREME_VOLATILITY = "extreme_volatility"
    ILLIQUID = "illiquid"


@dataclass(frozen=True, kw_only=True)
class Event:
    id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = field(default_factory=utc_now)
    correlation_id: str | None = None

    def payload(self) -> dict[str, Any]:
        def encode(item: Any) -> Any:
            if isinstance(item, datetime): return item.isoformat()
            if isinstance(item, Enum): return item.value
            if isinstance(item, dict): return {key: encode(value) for key,value in item.items()}
            if isinstance(item, (list,tuple)): return [encode(value) for value in item]
            return item
        return encode(asdict(self))


@dataclass(frozen=True, kw_only=True)
class PriceTick(Event):
    instrument: str
    bid: float
    ask: float
    volume: float = 0.0
    source: str = "unknown"

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread_ratio(self) -> float:
        return (self.ask - self.bid) / self.mid if self.mid else float("inf")


@dataclass(frozen=True, kw_only=True)
class CandleUpdate(Event):
    instrument: str
    timeframe: str
    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    tick_count: int = 0
    source: str = "unknown"
    complete: bool = True


@dataclass(frozen=True, kw_only=True)
class NewsEvent(Event):
    headline: str
    body: str
    source: str = "unknown"
    instruments: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class EconomicEvent(Event):
    name: str
    actual: float | None = None
    forecast: float | None = None
    currency: str | None = None
    scheduled_at: datetime | None = None
    importance: str = "normal"
    event_type: str = "other"


@dataclass(frozen=True, kw_only=True)
class SignalEvent(Event):
    instrument: str
    side: Side
    strength: float
    strategy: str
    price: float
    indicators: dict[str, float | None] = field(default_factory=dict)
    regime: str = "unknown"
    reason: str = ""
    strategy_version: str = "unknown"


@dataclass(frozen=True, kw_only=True)
class OrderRequest(Event):
    instrument: str
    side: Side
    quantity: float
    expected_price: float
    stop_price: float | None = None
    signal_id: str | None = None
    take_profit_price: float | None = None
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None


@dataclass(frozen=True, kw_only=True)
class OrderUpdate(Event):
    order_id: str
    instrument: str
    side: Side
    quantity: float
    status: OrderStatus
    fill_price: float | None = None
    reason: str | None = None


@dataclass(frozen=True, kw_only=True)
class PositionUpdate(Event):
    instrument: str
    quantity: float
    average_price: float
    realized_pnl: float


@dataclass(frozen=True, kw_only=True)
class RiskEvent(Event):
    approved: bool
    rule: str
    message: str
    order_request_id: str | None = None
    severity: str = "info"


@dataclass(frozen=True, kw_only=True)
class EmergencyEvent(Event):
    reason: str
    kill_switch_active: bool


@dataclass(frozen=True, kw_only=True)
class RegimeEvent(Event):
    instrument: str
    previous: Regime
    current: Regime
    metrics: dict[str, float]


@dataclass(frozen=True, kw_only=True)
class PortfolioSnapshot(Event):
    cash: float
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    exposure: float
    peak_equity: float
    drawdown: float
    trading_state: str
    positions: dict[str, dict[str, float]]


@dataclass(frozen=True, kw_only=True)
class AIAnalysisResult(Event):
    job_id: str
    instrument: str
    event_type: str
    direction: str
    importance: float
    confidence: float
    time_horizon: str
    summary: str
    evidence: tuple[str, ...]
    available: bool = True
    error: str | None = None
