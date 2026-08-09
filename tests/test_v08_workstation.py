import time
import unittest
from pathlib import Path

from feline.gui.controller import ChartBuffer,WorkstationController
from feline.replay.mixed import read_mixed_events,replay_format
from feline.core.events import PriceTick
from feline.macro.events import NormalizedEconomicEvent

FIXTURES=Path(__file__).parent/"fixtures"

class V08WorkstationTests(unittest.TestCase):
 def test_format_detection_and_ordering(self):
  self.assertEqual(replay_format(FIXTURES/"sample_ticks.csv"),"csv")
  self.assertEqual(replay_format(FIXTURES/"fed_macro.jsonl"),"jsonl")
  with self.assertRaises(ValueError):replay_format(Path("bad.parquet"))
  events=read_mixed_events(FIXTURES/"fed_macro.jsonl")
  stamps=[x.timestamp if isinstance(x,PriceTick) else x.scheduled_at for x in events]
  self.assertEqual(stamps,sorted(stamps));self.assertTrue(any(isinstance(x,NormalizedEconomicEvent) for x in events))
 def test_chart_fit_is_one_shot(self):
  chart=ChartBuffer();chart.add(1,1.1);self.assertTrue(chart.consume_fit());self.assertFalse(chart.consume_fit());chart.request_fit();self.assertTrue(chart.consume_fit())
 def _scenario(self,name,outcome):
  controller=WorkstationController();controller.start_replay(str(FIXTURES/name),"MAX");controller.future.result(timeout=5);snapshot=controller.snapshot();self.assertEqual(snapshot["strategy"]["state"],outcome);self.assertIn(1,snapshot["horizons"]);self.assertIn(5,snapshot["horizons"]);self.assertEqual(controller.replay.state.value,"stopped");controller.shutdown()
 def test_continuation(self):self._scenario("macro_continuation.jsonl","CONTINUATION")
 def test_mean_reversion(self):self._scenario("macro_mean_reversion.jsonl","MEAN_REVERSION")
 def test_no_trade(self):self._scenario("macro_no_trade.jsonl","NO_TRADE")
 def test_consecutive_mixed_replays(self):
  controller=WorkstationController()
  for name in ("macro_continuation.jsonl","macro_mean_reversion.jsonl"):
   controller.start_replay(str(FIXTURES/name),"MAX");controller.future.result(timeout=5)
  self.assertEqual(controller.snapshot()["strategy"]["state"],"MEAN_REVERSION");controller.shutdown()

if __name__=="__main__":unittest.main()
