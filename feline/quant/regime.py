from __future__ import annotations

from dataclasses import dataclass

from feline.core.events import Regime, RegimeEvent


@dataclass(frozen=True)
class RegimeConfig:
    minimum_samples: int = 5
    trend_threshold: float = 0.002
    high_volatility: float = 0.01
    extreme_volatility: float = 0.03
    max_spread: float = 0.01


class RegimeDetector:
    def __init__(self, config: RegimeConfig | None = None) -> None:
        self.config = config or RegimeConfig()
        self.current: dict[str, Regime] = {}

    def classify(self, *, samples: int, momentum: float | None, volatility: float | None, spread: float) -> Regime:
        if samples < self.config.minimum_samples or momentum is None or volatility is None: return Regime.INSUFFICIENT_DATA
        if spread > self.config.max_spread: return Regime.ILLIQUID
        if volatility >= self.config.extreme_volatility: return Regime.EXTREME_VOLATILITY
        if volatility >= self.config.high_volatility: return Regime.HIGH_VOLATILITY
        if abs(momentum) >= self.config.trend_threshold: return Regime.TRENDING
        return Regime.NORMAL

    def update(self, instrument: str, *, samples: int, momentum: float | None, volatility: float | None, spread: float) -> RegimeEvent | None:
        new = self.classify(samples=samples, momentum=momentum, volatility=volatility, spread=spread)
        previous = self.current.get(instrument, Regime.INSUFFICIENT_DATA)
        self.current[instrument] = new
        if new == previous: return None
        return RegimeEvent(instrument=instrument, previous=previous, current=new, metrics={"samples": float(samples), "momentum": momentum or 0.0, "volatility": volatility or 0.0, "spread": spread})
