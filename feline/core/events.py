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
    NEW = "new"
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    PENDING = "pending"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    TRIGGERED = "triggered"
    EXPIRED = "expired"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


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
    replay_session_id: str | None = None

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
    ingestion_timestamp: datetime | None = None
    provider_sequence: int | None = None
    realtime_session_id: str | None = None

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
    provenance: str = "reconstructed"

    def __post_init__(self) -> None:
        if self.close_time <= self.open_time: raise ValueError("candle close_time must follow open_time")
        if self.high < max(self.open,self.close,self.low): raise ValueError("candle high is below OHLC value")
        if self.low > min(self.open,self.close,self.high): raise ValueError("candle low is above OHLC value")
        if self.provenance not in {"native","reconstructed"}: raise ValueError("invalid candle provenance")


@dataclass(frozen=True, kw_only=True)
class NewsEvent(Event):
    headline: str
    body: str
    source: str = "unknown"
    instruments: tuple[str, ...] = ()
    ingestion_timestamp: datetime | None = None
    source_url: str | None = None
    provider_event_id: str | None = None


class ThesisState(str,Enum):
    CREATED="CREATED";WATCHING="WATCHING";CONFIRMED="CONFIRMED";REJECTED="REJECTED";INVALIDATED="INVALIDATED";EXPIRED="EXPIRED";RESEARCH_ONLY="RESEARCH_ONLY"


@dataclass(frozen=True)
class AffectedAsset:
    instrument:str
    directional_bias:str
    confidence:float
    relevance:float
    expected_horizon:str
    rationale:str
    monitoring_priority:float
    tradable:bool=False
    shortable:bool|None=None
    broker_status:str="unknown"
    underlying:str|None=None
    causal_effect:str="UNCERTAIN"


@dataclass(frozen=True,kw_only=True)
class MarketThesis(Event):
    schema_version:str="market-thesis-v1"
    thesis_id:str
    ai_job_id:str
    created_at:datetime
    catalyst_event_id:str
    catalyst_type:str
    source:str
    headline:str
    event_summary:str
    importance:float
    confidence:float
    expected_horizon:str
    expires_at:datetime
    reasoning_summary:str
    risk_warnings:tuple[str,...]=()
    invalidation_conditions:tuple[str,...]=()
    provider:str="unknown"
    model_identifier:str|None=None
    prompt_schema_version:str="news-market-impact-v1"
    prompt_hash:str|None=None
    context_hash:str|None=None
    latency_ms:float|None=None
    affected_assets:tuple[AffectedAsset,...]=()
    state:ThesisState=ThesisState.CREATED


@dataclass(frozen=True,kw_only=True)
class ThesisStateEvent(Event):
    thesis_id:str
    instrument:str
    previous:ThesisState
    current:ThesisState
    confirmation_state:str
    reason:str
    source_signal_id:str|None=None


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
    realtime_session_id: str | None = None


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
    expires_at: datetime | None = None


@dataclass(frozen=True, kw_only=True)
class OrderUpdate(Event):
    order_id: str
    instrument: str
    side: Side
    quantity: float
    status: OrderStatus
    fill_price: float | None = None
    reason: str | None = None
    filled_quantity: float = 0.0
    remaining_quantity: float = 0.0


@dataclass(frozen=True, kw_only=True)
class FillEvent(Event):
    order_id: str
    instrument: str
    side: Side
    quantity: float
    reference_price: float
    fill_price: float
    gross_value: float
    commission: float
    spread_cost: float
    slippage_amount: float
    slippage_percentage: float
    latency_ms: float
    assumptions: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class FinancingEvent(Event):
    instrument: str
    quantity: float
    days: int
    rate: float
    amount: float


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
class FeedHealthEvent(Event):
    provider: str
    state: str
    realtime_session_id: str
    last_source_timestamp: datetime | None = None
    last_ingestion_timestamp: datetime | None = None
    message: str = ""


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
    error_detail: str | None = None
    origin_event_ids: tuple[str, ...] = ()
    normalized_source: str | None = None
    publication_timestamp: datetime | None = None
    ingestion_timestamp: datetime | None = None
    model_identifier: str | None = None
    prompt_schema_version: str = "trading-assessment-v1"
    suggested_action: str = "NO_TRADE"
    reasoning_summary: str = ""
    event_relevance: float = 0.0
    risk_warnings: tuple[str, ...] = ()
    provider: str | None = None
    model_version: str | None = None
    prompt_hash: str | None = None
    context_hash: str | None = None
    context_timestamp: datetime | None = None
    expires_at: datetime | None = None
    latency_ms: float | None = None
    affected_signal_id: str | None = None
    downstream_decision: str = "advisory_only"
    vetoed: bool = False
    realtime_session_id: str | None = None
