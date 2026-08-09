from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from feline.core.events import CandleUpdate, PriceTick


TIMEFRAMES = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}


def floor_time(value: datetime, seconds: int) -> datetime:
    value = value.astimezone(timezone.utc)
    return datetime.fromtimestamp(int(value.timestamp()) // seconds * seconds, timezone.utc)


@dataclass
class _BuildingCandle:
    instrument: str
    timeframe: str
    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    tick_count: int
    source: str
    first_tick: datetime
    last_tick: datetime

    def add(self, price: float, volume: float, timestamp: datetime) -> None:
        self.high, self.low = max(self.high, price), min(self.low, price)
        if timestamp < self.first_tick:
            self.open, self.first_tick = price, timestamp
        if timestamp >= self.last_tick:
            self.close, self.last_tick = price, timestamp
        self.volume += volume
        self.tick_count += 1

    def event(self, complete: bool = True) -> CandleUpdate:
        return CandleUpdate(instrument=self.instrument, timeframe=self.timeframe, open_time=self.open_time, close_time=self.close_time, open=self.open, high=self.high, low=self.low, close=self.close, volume=self.volume, tick_count=self.tick_count, source=self.source, complete=complete, timestamp=self.close_time)


class CandleAggregator:
    """Streaming UTC candle builder; duplicate tick IDs are ignored."""

    def __init__(self, timeframes: tuple[str, ...] = ("1m", "5m", "15m", "1h")) -> None:
        self.timeframes = timeframes
        self.active: dict[tuple[str, str], _BuildingCandle] = {}
        self.seen: set[str] = set()

    def update(self, tick: PriceTick) -> list[CandleUpdate]:
        if tick.id in self.seen:
            return []
        self.seen.add(tick.id)
        if len(self.seen) > 100_000:
            self.seen.clear()
            self.seen.add(tick.id)
        completed: list[CandleUpdate] = []
        for timeframe in self.timeframes:
            seconds = TIMEFRAMES[timeframe]
            start = floor_time(tick.timestamp, seconds)
            key = (tick.instrument, timeframe)
            current = self.active.get(key)
            if current and start > current.open_time:
                completed.append(current.event())
                current = None
            if current and start < current.open_time:
                continue  # too late to revise an emitted candle
            if current is None:
                current = _BuildingCandle(tick.instrument, timeframe, start, start + timedelta(seconds=seconds), tick.mid, tick.mid, tick.mid, tick.mid, tick.volume, 1, tick.source, tick.timestamp, tick.timestamp)
                self.active[key] = current
            elif current.tick_count and tick.timestamp != current.last_tick:
                current.add(tick.mid, tick.volume, tick.timestamp)
        return completed

    def flush(self) -> list[CandleUpdate]:
        result = [value.event(complete=False) for value in self.active.values()]
        self.active.clear()
        return result
