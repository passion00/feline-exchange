import asyncio,tempfile,unittest
from pathlib import Path

from feline.config import AppConfig,StrategyConfig
from feline.core.events import CandleUpdate,NewsEvent,Regime,Side
from feline.news.pipeline import NewsPipeline
from feline.replay.engine import CSVReplayProvider
from feline.replay.report import calculate_report
from feline.strategy.reference import ReferenceStrategy


class ReplayStrategyTests(unittest.IsolatedAsyncioTestCase):
 async def test_replay_deterministic(self):
  path=Path("tests/fixtures/sample_ticks.csv")
  async def collect(): return [(x.timestamp,x.mid) async for x in CSVReplayProvider(path,seed=7).stream()]
  self.assertEqual(await collect(),await collect())

 async def test_strategy_provenance(self):
  strategy=ReferenceStrategy(StrategyConfig(fast_period=2,slow_period=3,atr_period=2))
  signal=None
  from datetime import datetime,timezone,timedelta
  base=datetime(2026,1,1,tzinfo=timezone.utc)
  for i,price in enumerate([1,1.1,1.2,1.3]):
   candle=CandleUpdate(instrument="X",timeframe="1m",open_time=base+timedelta(minutes=i),close_time=base+timedelta(minutes=i+1),open=price,high=price+.01,low=price-.01,close=price,volume=1,complete=True)
   signal=strategy.on_candle(candle,Regime.TRENDING) or signal
  self.assertIsNotNone(signal); self.assertEqual(signal.strategy_version,"0.2.0"); self.assertIn("atr",signal.indicators); self.assertTrue(signal.reason); self.assertTrue(signal.correlation_id)

 async def test_backtest_statistics(self):
  report=calculate_report(100,[100,110,105,120],[10,-5,15],2,4)
  self.assertEqual(report.number_of_trades,3); self.assertEqual(report.winning_trades,2); self.assertAlmostEqual(report.profit_factor,5); self.assertEqual(report.exposure_time_ratio,.5)

 async def test_duplicate_news(self):
  pipeline=NewsPipeline(); event=NewsEvent(headline="Federal Reserve rate decision",body="text",source="wire")
  first=pipeline.process(event); self.assertIsNotNone(first); self.assertEqual(first.entities,("USD",)); self.assertIsNone(pipeline.process(event))
