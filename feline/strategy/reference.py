from __future__ import annotations

from feline.config import StrategyConfig
from feline.core.events import CandleUpdate, Regime, Side, SignalEvent
from feline.quant.framework import IndicatorState


class ReferenceStrategy:
    NAME = "reference"
    VERSION = "0.2.0"

    def __init__(self, config: StrategyConfig) -> None:
        self.config = config
        self.states: dict[str, IndicatorState] = {}
        self.last_side: dict[str, Side] = {}

    def on_candle(self, candle: CandleUpdate, regime: Regime) -> SignalEvent | None:
        if candle.timeframe != "1m" or not candle.complete: return None
        state = self.states.setdefault(candle.instrument, IndicatorState())
        state.update(candle.close,candle.high,candle.low,candle.close_time.timestamp())
        fast, slow = state.sma(self.config.fast_period), state.sma(self.config.slow_period)
        atr = state.atr(self.config.atr_period)
        momentum = state.momentum(self.config.fast_period)
        if None in (fast,slow,atr,momentum) or regime in {Regime.ILLIQUID,Regime.EXTREME_VOLATILITY,Regime.INSUFFICIENT_DATA}: return None
        side = Side.BUY if fast > slow and momentum > 0 else Side.SELL if fast < slow and momentum < 0 else None
        if side is None or self.last_side.get(candle.instrument) is side: return None
        self.last_side[candle.instrument] = side
        indicators = {"sma_fast":fast,"sma_slow":slow,"atr":atr,"momentum":momentum}
        return SignalEvent(instrument=candle.instrument,side=side,strength=min(1.0,abs(fast-slow)/(atr or 1)),strategy=self.NAME,price=candle.close,indicators=indicators,regime=regime.value,reason="fast/slow SMA alignment with momentum",strategy_version=self.VERSION,timestamp=candle.close_time,correlation_id=candle.id)

    def order_from_signal(self, signal: SignalEvent, equity: float) -> tuple[float,float,float]:
        atr = float(signal.indicators["atr"] or 0)
        stop_distance = max(atr * 1.5, signal.price * 0.0001)
        quantity = min(self.config.max_signal_quantity, equity*self.config.risk_fraction/stop_distance)
        stop = signal.price-stop_distance if signal.side is Side.BUY else signal.price+stop_distance
        target = signal.price+stop_distance*2 if signal.side is Side.BUY else signal.price-stop_distance*2
        return quantity,stop,target
