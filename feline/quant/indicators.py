from __future__ import annotations

from collections import deque
import math


class RollingReturns:
    def __init__(self, window: int = 20) -> None:
        self.prices: deque[float] = deque(maxlen=window + 1)

    def update(self, price: float) -> float | None:
        previous = self.prices[-1] if self.prices else None
        self.prices.append(price)
        return price / previous - 1 if previous and previous > 0 else None

    @property
    def volatility(self) -> float:
        values = list(self.prices)
        returns = [values[i] / values[i - 1] - 1 for i in range(1, len(values)) if values[i - 1]]
        if len(returns) < 2:
            return 0.0
        mean = sum(returns) / len(returns)
        return math.sqrt(sum((x - mean) ** 2 for x in returns) / (len(returns) - 1))


def simple_moving_average(values: list[float], period: int) -> float | None:
    return sum(values[-period:]) / period if period > 0 and len(values) >= period else None


def rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) <= period:
        return None
    changes = [values[i] - values[i - 1] for i in range(len(values) - period, len(values))]
    gains = sum(max(change, 0) for change in changes) / period
    losses = sum(max(-change, 0) for change in changes) / period
    if losses == 0:
        return 100.0
    return 100 - (100 / (1 + gains / losses))

