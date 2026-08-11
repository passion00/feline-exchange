from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from feline.config import AIConfig, AppConfig, ConfirmationConfig, ThesisConfig
from feline.core.events import AffectedAsset, MarketThesis, NewsEvent, PriceTick, Side, SignalEvent, ThesisState
from feline.experiments.models import ExperimentCase, SemanticExpectation
from feline.experiments.runner import _run_case
from feline.intelligence.operations import LocalAIProcessManager
from feline.intelligence.service import AIWorker, AnalysisJob, LlamaCppClient, timeout_for_job
from feline.runtime import FelineRuntime


UTC=timezone.utc;NOW=datetime(2026,1,1,12,tzinfo=UTC)


def impact():
 return {"event_type":"supply","event_summary":"oil disruption","importance":.9,"confidence":.9,"expected_horizon":"2 hours","affected_instruments":[{"instrument":"WTI","directional_bias":"LONG","confidence":.9,"relevance":.9,"monitoring_priority":.9,"rationale":"supply"}],"reasoning_summary":"supply disruption","risk_warnings":[],"invalidation_conditions":["supply restored"]}


def news_job():
 event=NewsEvent(id="n",timestamp=NOW,ingestion_timestamp=NOW,headline="Oil disruption",body="exports stopped",source="fixture")
 return AnalysisJob(event,purpose="analyze_news_for_market_impact",context={"instrument_universe":[{"instrument":"WTI","tradable":True,"shortable":True}],"default_expiry_minutes":240},context_timestamp=NOW,expires_at=NOW+timedelta(hours=4))


class TimeoutPolicyTests(unittest.TestCase):
 def test_defaults_and_purpose_separation(self):
  config=AIConfig();self.assertEqual(timeout_for_job(config,news_job()),300);self.assertEqual(timeout_for_job(config,"signal_assessment"),30);self.assertEqual(timeout_for_job(config,"news_assessment"),30)
  legacy=AIConfig(request_timeout_seconds=17,news_thesis_timeout_seconds=250,trading_assessment_timeout_seconds=11);self.assertEqual(timeout_for_job(legacy,"news_assessment"),17);self.assertEqual(timeout_for_job(legacy,news_job()),250);self.assertEqual(timeout_for_job(legacy,"signal_assessment"),11)

 def test_client_transport_uses_same_job_timeout(self):
  class Response:
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def read(self):return json.dumps({"choices":[{"message":{"content":json.dumps(impact())}}]}).encode()
  seen=[]
  def opener(request,timeout):seen.append(timeout);return Response()
  signal=AnalysisJob(NewsEvent(timestamp=NOW,headline="signal",body="",instruments=("EURUSD",)),purpose="signal_assessment")
  with patch("feline.intelligence.service.urlrequest.urlopen",side_effect=opener):self.assertEqual(LlamaCppClient(AIConfig())._request(news_job()),impact());LlamaCppClient(AIConfig())._request(signal)
  self.assertEqual(seen,[300,30])

 def test_config_rejects_invalid_purpose_deadlines(self):
  with self.assertRaises(ValueError):AIConfig(news_thesis_timeout_seconds=0)
  with self.assertRaises(ValueError):AIConfig(trading_assessment_timeout_seconds=-1)


class WorkerDeadlineTests(unittest.IsolatedAsyncioTestCase):
 async def _run_virtual_completion(self,virtual_seconds):
  captured=[];outputs=[]
  class Client:
   provider_name="fixture"
   async def analyze(self,job):return impact()
  original=asyncio.wait_for
  async def virtual(awaitable,timeout):captured.append(timeout);self.assertGreater(timeout,virtual_seconds);return await awaitable
  worker=AIWorker(AIConfig(retries=0),Client(),outputs.append)
  with patch("feline.intelligence.service.asyncio.wait_for",side_effect=virtual):
   worker.start();worker.submit_nowait(news_job());await worker.queue.join();await worker.stop()
  self.assertIsInstance(outputs[0],MarketThesis);self.assertGreaterEqual(captured[0],299)

 async def test_completions_after_30_120_and_just_before_300_are_accepted(self):
  for seconds in (31,120,299):await self._run_virtual_completion(seconds)

 async def test_worker_timeout_uses_news_deadline_and_fails_safe(self):
  outputs=[];captured=[]
  class Client:
   provider_name="fixture"
   async def analyze(self,job):return impact()
  async def timeout(awaitable,seconds):captured.append(seconds);awaitable.close();raise TimeoutError()
  worker=AIWorker(AIConfig(retries=0),Client(),outputs.append)
  with patch("feline.intelligence.service.asyncio.wait_for",side_effect=timeout):
   worker.start();worker.submit_nowait(news_job());await worker.queue.join();await worker.stop()
  self.assertGreaterEqual(captured[0],299);self.assertFalse(outputs[0].available);self.assertEqual(outputs[0].error,"TimeoutError")

 async def test_queue_remains_bounded_during_long_job(self):
  gate=asyncio.Event()
  class Slow:
   async def analyze(self,job):await gate.wait();return impact()
  worker=AIWorker(AIConfig(queue_size=2,retries=0),Slow(),lambda value:None);worker.start();self.assertTrue(worker.submit_nowait(news_job()));await asyncio.sleep(0);self.assertTrue(worker.busy);self.assertEqual(worker.active_job.event.headline,"Oil disruption");self.assertIsNotNone(worker.active_elapsed_seconds());self.assertTrue(worker.submit_nowait(news_job()));self.assertTrue(worker.submit_nowait(news_job()));self.assertFalse(worker.submit_nowait(news_job()));self.assertEqual(worker.queue.qsize(),2);gate.set();await worker.queue.join();await worker.stop()


class NewsFreshnessTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.temp=tempfile.TemporaryDirectory();config=AppConfig(database_path=str(Path(self.temp.name)/"x.db"),ai=AIConfig(enabled=True,decision_mode="confirm_or_veto",context_max_age_seconds=20,maximum_price_move_fraction=.001),thesis=ThesisConfig(maximum_reference_move_fraction=.01),confirmation=ConfirmationConfig(minimum_signal_strength=0));self.runtime=FelineRuntime(config,recover=False,autonomous_trading_enabled=False)
 async def asyncTearDown(self):
  await self.runtime.stop();self.runtime.database.close();self.temp.cleanup()
 def thesis(self,latency=120000):
  asset=AffectedAsset("EURUSD","LONG",.9,.9,"2 hours","reason",.9,True,True)
  return MarketThesis(id="t",thesis_id="t",ai_job_id="j",timestamp=NOW,created_at=NOW,catalyst_event_id="n",catalyst_type="macro",source="fixture",headline="news",event_summary="summary",importance=.9,confidence=.9,expected_horizon="2 hours",expires_at=NOW+timedelta(hours=2),reasoning_summary="reason",latency_ms=latency,affected_assets=(asset,),state=ThesisState.CREATED)
 async def test_20_second_signal_freshness_does_not_reject_slow_news(self):
  self.runtime.broker.update_quote(PriceTick(timestamp=NOW+timedelta(seconds=120),instrument="EURUSD",bid=1.1,ask=1.1001));await self.runtime._handle_ai_result(self.thesis());self.assertTrue(self.runtime.focus.watching("EURUSD",NOW+timedelta(seconds=121)))
 async def test_price_move_during_inference_keeps_thesis_but_later_entry_can_stale(self):
  self.runtime.broker.update_quote(PriceTick(timestamp=NOW,instrument="EURUSD",bid=1,ask=1.0001));self.runtime.broker.update_quote(PriceTick(timestamp=NOW+timedelta(seconds=120),instrument="EURUSD",bid=1.1,ask=1.1001));await self.runtime._handle_ai_result(self.thesis());entry=self.runtime.focus.watching("EURUSD",NOW+timedelta(seconds=121))[0];self.assertAlmostEqual(entry.reference_price,1.10005)
  signal=SignalEvent(timestamp=NOW+timedelta(minutes=3),instrument="EURUSD",side=Side.BUY,strength=1,strategy="reference",price=1.2,indicators={"atr":.001});candidate,reason=self.runtime.thesis_confirmation.evaluate(signal,entry);self.assertIsNone(candidate);self.assertEqual(reason,"STALE_MOVE");self.assertEqual(entry.state,ThesisState.WATCHING)
 async def test_analysis_only_expires_when_meaningful_thesis_horizon_passes(self):
  await self.runtime._handle_ai_result(self.thesis(latency=2*60*60*1000+1));self.assertEqual(self.runtime.latest_thesis.state,ThesisState.EXPIRED);self.assertFalse(self.runtime.focus.focus)
 async def test_short_signal_freshness_remains_short(self):
  signal=SignalEvent(timestamp=NOW,instrument="EURUSD",side=Side.BUY,strength=1,strategy="reference",price=1,indicators={"atr":.001});self.assertTrue(self.runtime._submit_signal_assessment(signal));pending=next(iter(self.runtime.pending_ai.values()));self.assertEqual((pending.expires_at-pending.context_timestamp).total_seconds(),20);self.assertEqual(timeout_for_job(self.runtime.config.ai,"signal_assessment"),30)


class ExperimentTimeoutAccountingTests(unittest.IsolatedAsyncioTestCase):
 async def test_timeout_is_not_abstention_or_cascade_of_failures(self):
  item=ExperimentCase("timeout","failure","h","b","fixture",NOW.isoformat(),({"instrument":"EURUSD","asset_class":"fx","shortable":True},),SemanticExpectation(True,("EURUSD",),("LONG",),("fx",),False),fixture_analysis={"event_type":"x","instruments":["EURUSD"],"bias":"LONG"},failure_mode="timeout",price_scenario="none")
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);result=await _run_case(item,AppConfig(database_path=str(root/"normal.db"),ai=AIConfig()),root/"experiment.db","fixture")
  self.assertEqual(result["ai"]["status"],"TIMEOUT");self.assertEqual(result["semantic"]["category"],"not_evaluated");self.assertEqual(result["semantic"]["evaluation_status"],"TIMEOUT");self.assertEqual(result["engineering"]["schema_status"],"NOT_EVALUATED");self.assertEqual(result["engineering"]["thesis_persistence_status"],"NOT_APPLICABLE");self.assertEqual(result["engineering"]["lifecycle_status"],"NOT_APPLICABLE");self.assertTrue(result["engineering"]["passed"]);self.assertEqual(result["execution"]["external_orders"],0)
 async def test_semantic_direction_error_is_not_a_software_safety_failure(self):
  item=ExperimentCase("wrong_direction","test","h","b","fixture",NOW.isoformat(),({"instrument":"EURUSD","asset_class":"fx","shortable":True},),SemanticExpectation(True,("EURUSD",),("LONG",),("fx",),True),fixture_analysis={"event_type":"x","instruments":["EURUSD"],"bias":"SHORT"},price_scenario="confirms_up")
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);result=await _run_case(item,AppConfig(database_path=str(root/"normal.db"),ai=AIConfig()),root/"experiment.db","fixture")
  self.assertIn(result["semantic"]["category"],{"partial_match","mismatch"});self.assertEqual(result["execution"]["confirmation_candidates"],0);self.assertTrue(result["engineering"]["passed"])


class ReasoningCapabilityTests(unittest.TestCase):
 def _assets(self,td):
  root=Path(td);model=root/"model.gguf";model.write_bytes(b"m");exe=root/"llama-server";exe.write_text("#!/bin/sh\n");exe.chmod(0o755)
  class Assets:
   selected_model_id="fixture"
   def runtime_executable(self):return exe
   def model_path(self):return model
  return root,Assets()
 def test_reasoning_disabled_uses_actual_reasoning_off_flag(self):
  with tempfile.TemporaryDirectory() as td:
   root,assets=self._assets(td);manager=LocalAIProcessManager(root=root)
   with patch.object(manager,"runtime_supports",side_effect=lambda exe,flag:flag=="--reasoning"):
    argv,warnings=manager.build_argv(AIConfig(reasoning_mode="disabled"),assets)
   self.assertIn("--reasoning",argv);self.assertEqual(argv[argv.index("--reasoning")+1],"off");self.assertNotIn("--reasoning-format",argv);self.assertFalse(warnings)
 def test_missing_capability_degrades_with_warning(self):
  with tempfile.TemporaryDirectory() as td:
   root,assets=self._assets(td);manager=LocalAIProcessManager(root=root)
   with patch.object(manager,"runtime_supports",return_value=False):argv,warnings=manager.build_argv(AIConfig(reasoning_mode="disabled"),assets)
   self.assertNotIn("--reasoning",argv);self.assertTrue(warnings)


if __name__=="__main__":unittest.main()
