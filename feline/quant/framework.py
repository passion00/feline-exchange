from __future__ import annotations

from collections import deque
import math


class IndicatorState:
    def __init__(self, maxlen: int = 512) -> None:
        self.closes: deque[float] = deque(maxlen=maxlen)
        self.highs: deque[float] = deque(maxlen=maxlen)
        self.lows: deque[float] = deque(maxlen=maxlen)
        self.times: deque[float] = deque(maxlen=maxlen)
        self._ema: dict[int, float] = {}

    def update(self, close: float, high: float | None = None, low: float | None = None, timestamp: float | None = None) -> None:
        self.closes.append(close); self.highs.append(high if high is not None else close); self.lows.append(low if low is not None else close)
        self.times.append(timestamp if timestamp is not None else float(len(self.times)))

    def sma(self, period: int) -> float | None:
        return sum(list(self.closes)[-period:]) / period if len(self.closes) >= period else None

    def ema(self, period: int) -> float | None:
        if not self.closes: return None
        previous = self._ema.get(period, self.closes[0]); alpha = 2 / (period + 1)
        value = alpha * self.closes[-1] + (1 - alpha) * previous; self._ema[period] = value; return value

    def rsi(self, period: int = 14) -> float | None:
        values = list(self.closes)
        if len(values) <= period: return None
        changes = [values[i] - values[i-1] for i in range(len(values)-period, len(values))]
        gain = sum(max(x, 0) for x in changes) / period; loss = sum(max(-x, 0) for x in changes) / period
        return 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)

    def atr(self, period: int = 14) -> float | None:
        if len(self.closes) <= period: return None
        c, h, l = list(self.closes), list(self.highs), list(self.lows)
        trs = [max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1])) for i in range(len(c)-period, len(c))]
        return sum(trs) / period

    def volatility(self, period: int = 20) -> float | None:
        values = list(self.closes)[-(period+1):]
        if len(values) <= period: return None
        returns = [values[i]/values[i-1]-1 for i in range(1,len(values)) if values[i-1]]
        mean = sum(returns)/len(returns)
        return math.sqrt(sum((x-mean)**2 for x in returns)/(len(returns)-1)) if len(returns)>1 else 0.0

    def momentum(self, period: int = 10) -> float | None:
        return self.closes[-1] / list(self.closes)[-period-1] - 1 if len(self.closes) > period and list(self.closes)[-period-1] else None

    def velocity(self, period: int = 1) -> float | None:
        if len(self.closes) <= period: return None
        elapsed = self.times[-1] - list(self.times)[-period-1]
        return (self.closes[-1] - list(self.closes)[-period-1]) / elapsed if elapsed > 0 else None

    def rolling_high(self, period: int) -> float | None:
        return max(list(self.highs)[-period:]) if len(self.highs) >= period else None

    def rolling_low(self, period: int) -> float | None:
        return min(list(self.lows)[-period:]) if len(self.lows) >= period else None

    def drawdown(self, period: int | None = None) -> float | None:
        values = list(self.closes)[-period:] if period else list(self.closes)
        return (max(values)-values[-1])/max(values) if values and max(values)>0 else None


def spread_percentage(bid: float, ask: float) -> float:
    mid = (bid + ask) / 2
    return (ask-bid)/mid if mid else float("inf")
