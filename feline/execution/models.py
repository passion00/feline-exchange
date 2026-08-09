from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from feline.core.events import OrderRequest,OrderStatus


@dataclass
class PendingOrder:
    order_id:str
    request:OrderRequest
    state:OrderStatus
    remaining_quantity:float
    filled_quantity:float=0.0
    accepted_at:datetime|None=None
    eligible_at:datetime|None=None
    parent_order_id:str|None=None


@dataclass(frozen=True)
class FinancingRule:
    instrument:str
    long_daily_rate:float=0.0
    short_daily_rate:float=0.0
    rollover_hour_utc:int=21
    triple_day:int=2
