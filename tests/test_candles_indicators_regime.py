import unittest
from datetime import datetime,timezone,timedelta

from feline.core.events import PriceTick,Regime
from feline.market.candles import CandleAggregator
from feline.quant.framework import IndicatorState,spread_percentage
from feline.quant.regime import RegimeConfig,RegimeDetector


class CandleIndicatorTests(unittest.TestCase):
 def test_candles_boundaries_duplicates_and_out_of_order(self):
  agg=CandleAggregator(); base=datetime(2026,1,1,0,0,tzinfo=timezone.utc)
  first=PriceTick(id="a",timestamp=base+timedelta(seconds=40),instrument="X",bid=9,ask=11,volume=2,source="test")
  self.assertEqual(agg.update(first),[]); self.assertEqual(agg.update(first),[])
  agg.update(PriceTick(timestamp=base+timedelta(seconds=10),instrument="X",bid=8,ask=10,volume=1))
  done=agg.update(PriceTick(timestamp=base+timedelta(minutes=1),instrument="X",bid=11,ask=13,volume=3))
  one=next(x for x in done if x.timeframe=="1m")
  self.assertEqual((one.open,one.high,one.low,one.close,one.volume,one.tick_count),(9,10,9,10,3,2))
  self.assertFalse(agg.flush()[0].complete)

 def test_indicator_framework(self):
  state=IndicatorState()
  for i,v in enumerate([10,11,12,11,13,14]):state.update(v,v+1,v-1,float(i))
  self.assertEqual(state.sma(3),38/3); self.assertIsNotNone(state.ema(3)); self.assertIsNotNone(state.rsi(3)); self.assertIsNotNone(state.atr(3)); self.assertIsNotNone(state.volatility(3)); self.assertAlmostEqual(state.momentum(2),14/11-1); self.assertEqual(state.velocity(2),1.5); self.assertEqual(state.rolling_high(3),15); self.assertEqual(state.rolling_low(3),10); self.assertAlmostEqual(spread_percentage(99,101),.02)

 def test_regimes_and_transition(self):
  detector=RegimeDetector(RegimeConfig(minimum_samples=3,trend_threshold=.01,high_volatility=.02,extreme_volatility=.04,max_spread=.03))
  self.assertEqual(detector.classify(samples=1,momentum=None,volatility=None,spread=0),Regime.INSUFFICIENT_DATA)
  self.assertEqual(detector.classify(samples=4,momentum=.02,volatility=.01,spread=0),Regime.TRENDING)
  self.assertEqual(detector.classify(samples=4,momentum=0,volatility=.05,spread=0),Regime.EXTREME_VOLATILITY)
  self.assertEqual(detector.classify(samples=4,momentum=0,volatility=0,spread=.04),Regime.ILLIQUID)
  self.assertIsNotNone(detector.update("X",samples=4,momentum=.02,volatility=.01,spread=0))
