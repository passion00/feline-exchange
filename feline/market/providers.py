from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from feline.core.events import EconomicEvent, NewsEvent, PriceTick


class MarketDataProvider(ABC):
    @abstractmethod
    def stream(self) -> AsyncIterator[PriceTick]: ...


class NewsProvider(ABC):
    @abstractmethod
    def stream(self) -> AsyncIterator[NewsEvent]: ...


class EconomicCalendarProvider(ABC):
    @abstractmethod
    def stream(self) -> AsyncIterator[EconomicEvent]: ...


class MockMarketDataProvider(MarketDataProvider):
    def __init__(self, interval: float = 0.25, instrument: str = "EURUSD") -> None:
        self.interval = interval
        self.instrument = instrument
        self.sequence = 0

    async def stream(self) -> AsyncIterator[PriceTick]:
        while True:
            self.sequence += 1
            mid = 1.10 + ((self.sequence % 20) - 10) * 0.00001
            yield PriceTick(instrument=self.instrument, bid=mid - 0.00005, ask=mid + 0.00005, volume=100)
            await asyncio.sleep(self.interval)

