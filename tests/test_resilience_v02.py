import asyncio,tempfile,unittest
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from pathlib import Path

from feline.config import AIConfig,AppConfig,RiskConfig
from feline.core.events import EconomicEvent,NewsEvent,OrderRequest,PriceTick,Side
from feline.intelligence.service import AIWorker,AnalysisJob,JobPriority
from feline.market.providers import MarketDataProvider
from feline.replay.engine import CSVReplayProvider
from feline.runtime import FelineRuntime


VALID={"instrument":"EURUSD","event_type":"macro","direction":"neutral","importance":.5,"confidence":.5,"time_horizon":"minutes","summary":"x","evidence":[]}
class SlowAI:
 async def analyze(self,job):await asyncio.sleep(.3);return VALID
class FailingProvider(MarketDataProvider):
 async def stream(self):
  if False:yield
  raise ConnectionError("offline")

class ResilienceTests(unittest.IsolatedAsyncioTestCase):
 async def test_ai_queue_overload_is_bounded(self):
  worker=AIWorker(AIConfig(queue_size=2,request_timeout_seconds=1),SlowAI(),lambda x:None)
  self.assertTrue(worker.submit_nowait(AnalysisJob(NewsEvent(headline="a",body=""),priority=JobPriority.LOW)))
  self.assertTrue(worker.submit_nowait(AnalysisJob(NewsEvent(headline="b",body=""),priority=JobPriority.CRITICAL)))
  self.assertFalse(worker.submit_nowait(AnalysisJob(NewsEvent(headline="c",body=""))))
  self.assertEqual(worker.queue.qsize(),2);self.assertEqual(worker.dropped,1)

 async def test_critical_ai_replaces_low_when_full(self):
  worker=AIWorker(AIConfig(queue_size=1),SlowAI(),lambda x:None)
  self.assertTrue(worker.submit_nowait(AnalysisJob(NewsEvent(headline="low",body=""),priority=JobPriority.LOW)))
  self.assertTrue(worker.submit_nowait(AnalysisJob(NewsEvent(headline="critical",body=""),priority=JobPriority.CRITICAL)))
  _,_,job=worker.queue.get_nowait();self.assertEqual(job.priority,JobPriority.CRITICAL);worker.queue.task_done()

 async def test_provider_failure_is_graceful(self):
  with tempfile.TemporaryDirectory() as d:
   runtime=FelineRuntime(AppConfig(database_path=str(Path(d)/"x.db")),provider=FailingProvider())
   await runtime.run();await runtime.stop();self.assertEqual(runtime.database.count("portfolio_snapshots"),1);runtime.database.close()

 async def test_twenty_second_shock_ai_irrelevant_and_risk_cannot_bypass(self):
  with tempfile.TemporaryDirectory() as d:
   config=replace(AppConfig(database_path=str(Path(d)/"x.db"),snapshot_interval_ticks=5),risk=replace(RiskConfig(),emergency_volatility_threshold=.005,event_minutes_before=1,event_minutes_after=1),ai=AIConfig(request_timeout_seconds=2))
   runtime=FelineRuntime(config,ai_client=SlowAI());runtime.ai.start();now=datetime.now(timezone.utc)
   runtime.schedule_economic_event(EconomicEvent(name="Central bank decision",scheduled_at=now,importance="critical"))
   runtime.submit_news(NewsEvent(headline="Central bank rate decision",body="untrusted",instruments=("EURUSD",)))
   for second in range(21):
    mid=1.0 if second<5 else 1.0+(second-4)*.03
    await runtime.handle_tick(PriceTick(timestamp=now+timedelta(seconds=second),instrument="EURUSD",bid=mid-.001,ask=mid+.001))
   request=OrderRequest(timestamp=now+timedelta(seconds=20),instrument="EURUSD",side=Side.BUY,quantity=1,expected_price=1.48,stop_price=1.4)
   decision=await runtime.request_order(request)
   self.assertFalse(decision.approved);self.assertTrue(runtime.risk.kill_switch);self.assertGreaterEqual(runtime.database.count("market_events"),0)
   self.assertFalse(runtime.ai.task.done());await runtime.stop();runtime.database.close()

 async def test_kill_switch_during_replay_and_safe_restart(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/"x.db";config=AppConfig(database_path=str(path),risk=replace(RiskConfig(),emergency_volatility_threshold=.00001))
   runtime=FelineRuntime(config,provider=CSVReplayProvider(Path("tests/fixtures/sample_ticks.csv")));await runtime.run();self.assertTrue(runtime.risk.kill_switch);runtime.snapshot();await runtime.stop();runtime.database.close()
   restarted=FelineRuntime(config);self.assertIsNotNone(restarted.database.latest_portfolio());restarted.database.close()
