from __future__ import annotations
from datetime import datetime,timedelta,timezone
from dataclasses import replace
import json,tempfile,unittest
from pathlib import Path
from feline.config import AppConfig,AIConfig
from feline.core.events import CandleUpdate
from feline.gui.controller import ChartBuffer,WorkstationController
from feline.macro.events import measure_horizon
from feline.market.candles import NativeCandleAggregator
from feline.replay.mixed import read_mixed_events
from feline.replay.twelvedata import add_economic_event,convert_twelvedata_file

UTC=timezone.utc;FIXTURES=Path(__file__).parent/"fixtures"
def candle(minute,open_,high,low,close):
 start=datetime(2024,1,1,tzinfo=UTC)+timedelta(minutes=minute);return CandleUpdate(timestamp=start+timedelta(minutes=1),instrument="EURUSD",timeframe="1m",open_time=start,close_time=start+timedelta(minutes=1),open=open_,high=high,low=low,close=close,volume=1,source="test",provenance="native")

class NativeOHLCTests(unittest.TestCase):
 def test_validation(self):
  with self.assertRaises(ValueError):candle(0,1,0.9,.8,1)
  with self.assertRaises(ValueError):candle(0,1,1.1,1.01,1)
  with self.assertRaises(ValueError):CandleUpdate(timestamp=datetime(2024,1,1,tzinfo=UTC),instrument="X",timeframe="1m",open_time=datetime(2024,1,1,tzinfo=UTC),close_time=datetime(2024,1,1,tzinfo=UTC),open=1,high=1,low=1,close=1,volume=0)
 def test_twelvedata_reverse_order_and_close_timestamp(self):
  with tempfile.TemporaryDirectory() as tmp:
   out=Path(tmp)/"out.jsonl";self.assertEqual(convert_twelvedata_file(FIXTURES/"twelvedata_sample.json",out,"EURUSD"),3);rows=[json.loads(x) for x in out.read_text().splitlines()];self.assertEqual([x["open_time"] for x in rows],sorted(x["open_time"] for x in rows));self.assertEqual(rows[0]["timestamp"],"2024-09-18T18:01:00Z");self.assertEqual(rows[0]["open"],1.11);self.assertEqual(rows[0]["high"],1.112);self.assertEqual(rows[0]["provenance"],"native")
   merged=Path(tmp)/"mixed.jsonl";self.assertEqual(add_economic_event(out,merged,"2024-09-18T18:01:30Z","fed","FOMC"),4);events=read_mixed_events(merged);self.assertEqual(len(events),4)
 def test_mixed_order_uses_candle_completion_not_open(self):
  events=read_mixed_events(FIXTURES/"fomc_ohlc_2024.jsonl");timestamps=[x.timestamp if isinstance(x,CandleUpdate) else x.scheduled_at for x in events];self.assertEqual(timestamps,sorted(timestamps));first=next(x for x in events if isinstance(x,CandleUpdate));self.assertEqual(first.timestamp,first.close_time);self.assertGreater(first.timestamp,first.open_time)
 def test_native_aggregation_high_low_and_no_partial_flush(self):
  aggregator=NativeCandleAggregator();outputs=[]
  values=[candle(i,1+i*.01,1.02+i*.01,.98+i*.01,1.01+i*.01) for i in range(6)]
  for value in values:outputs.extend(aggregator.update(value))
  five=next(x for x in outputs if x.timeframe=="5m");self.assertEqual((five.open,five.high,five.low,five.close),(1,1.06,.98,1.05));self.assertEqual(five.volume,5);self.assertEqual(aggregator.flush(),[])
 def test_aggregation_15m_and_1h(self):
  aggregator=NativeCandleAggregator();outputs=[]
  for i in range(61):outputs.extend(aggregator.update(candle(i,1,1+i/1000,.9,1)))
  fifteen=[x for x in outputs if x.timeframe=="15m"];hour=[x for x in outputs if x.timeframe=="1h"];self.assertGreaterEqual(len(fifteen),4);self.assertEqual(len(hour),1);self.assertAlmostEqual(hour[0].high,1.059)
 def test_ohlc_extremes_improve_excursion(self):
  measured=measure_horizon([1,1.01],[0,0],5,[1,1.08],[1,.94]);self.assertAlmostEqual(measured.mfe,.08);self.assertAlmostEqual(measured.mae,-.06)
 def test_projection_bounded_and_timeframes(self):
  buffer=ChartBuffer(limit=2)
  for i in range(3):buffer.add_candle({"timeframe":"1m","open_timestamp":i*60,"close_timestamp":i*60+60,"open":1,"high":2,"low":.5,"close":1.1,"volume":0})
  buffer.add_candle({"timeframe":"5m","open_timestamp":0,"close_timestamp":300,"open":1,"high":2,"low":.5,"close":1.1,"volume":0});self.assertEqual(len(buffer.candles["1m"]),2);self.assertEqual(len(buffer.candles["5m"]),1)
 def test_native_replay_report_metadata_and_session_isolation(self):
  with tempfile.TemporaryDirectory() as tmp:
   config=replace(AppConfig(),database_path=str(Path(tmp)/"db.sqlite"),ai=AIConfig(enabled=False));controller=WorkstationController(config);controller.start_replay(str(FIXTURES/"fomc_ohlc_2024.jsonl"),"MAX");controller.future.result(timeout=5);report=controller.build_report();metadata=report["metadata"];self.assertEqual(metadata["source_data_type"],"native_ohlc");self.assertEqual(metadata["candle_timeframe"],"1m");self.assertTrue(metadata["ohlc_available"]);self.assertTrue(metadata["execution_bid_ask_synthetic"]);self.assertIn("synthetic",metadata["spread_provenance"]);self.assertTrue(report["reference_candles"]);sid=metadata["replay_session_id"];self.assertTrue(all(x["replay_session_id"]==sid for x in controller.records["candles"]));controller.shutdown()

if __name__=="__main__":unittest.main()
