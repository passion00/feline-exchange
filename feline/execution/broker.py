from __future__ import annotations

from abc import ABC, abstractmethod

from feline.core.events import OrderRequest, OrderUpdate, PriceTick
from feline.portfolio.models import Position


class Broker(ABC):
    @abstractmethod
    def get_balance(self) -> float: ...
    @abstractmethod
    def get_positions(self) -> dict[str, Position]: ...
    @abstractmethod
    def get_quote(self, instrument: str) -> PriceTick | None: ...
    @abstractmethod
    async def submit_order(self, request: OrderRequest) -> OrderUpdate: ...
    @abstractmethod
    async def cancel_order(self, order_id: str) -> OrderUpdate: ...
    @abstractmethod
    async def close_position(self, instrument: str) -> OrderUpdate: ...

