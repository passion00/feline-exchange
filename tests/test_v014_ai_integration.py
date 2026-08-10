from __future__ import annotations

import asyncio,tempfile,unittest
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from pathlib import Path

from feline.config import AIConfig,AppConfig
from feline.core.events import AIAnalysisResult,NewsEvent,PriceTick,Side,SignalEvent
from feline.intelligence.service import AIWorker,AnalysisJob,context_hash,prompt_hash,validate_analysis
from feline.runtime import FelineRuntime,PendingAIAssessment

UTC=timezone.utc

VALID={"instrument":"EURUSD","event_type":"market","direction":"up","importance":.7,"confidence":.9,"time_horizon":"minutes","summary":"aligned","reasoning_summary":"trend alignment","event_relevance":.2,"risk_warnings":["synthetic execution"],"suggested_action":"LONG","evidence":["completed candles"]}


class Client:
    provider_name="mock_ai"
    def __init__(self,result=None,delay=0,error=None):self.result=result;self.delay=delay;self.error=error
    async def analyze(self,job):
        await asyncio.sleep(self.delay)
        if self.error:raise self.error
        return self.result


def signal(now):return SignalEvent(timestamp=now,instrument="EURUSD",side=Side.BUY,strength=.8,strategy="reference",price=1.1,indicators={"atr":.001},regime="normal",reason="fixture",strategy_version="test")
def result(job_id,now,**changes):
    base=dict(job_id=job_id,instrument="EURUSD",event_type="market",direction="up",importance=.7,confidence=.9,time_horizon="minutes",summary="ok",reasoning_summary="ok",evidence=(),suggested_action="LONG",available=True,context_timestamp=now,expires_at=now+timedelta(seconds=20),affected_signal_id="s")
    base.update(changes);return AIAnalysisResult(**base)


class SchemaTests(unittest.TestCase):
    def test_structured_schema_hashes_and_metadata(self):
        now=datetime.now(UTC);job=AnalysisJob(NewsEvent(timestamp=now,headline="h",body="b",instruments=("EURUSD",)),context={"instrument":"EURUSD","price":1.1},purpose="signal_assessment",signal_id="s",context_timestamp=now,expires_at=now+timedelta(seconds=10),model_identifier="m")
        parsed=validate_analysis(VALID,job,"mock",12.5);self.assertEqual(parsed.suggested_action,"LONG");self.assertEqual(parsed.risk_warnings,("synthetic execution",));self.assertEqual(parsed.prompt_hash,prompt_hash(job));self.assertEqual(parsed.context_hash,context_hash(job));self.assertEqual(parsed.latency_ms,12.5)

    def test_malformed_action_and_instrument_rejected(self):
        job=AnalysisJob(NewsEvent(headline="h",body="b",instruments=("EURUSD",)),context={"instrument":"EURUSD"})
        with self.assertRaises(ValueError):validate_analysis({**VALID,"suggested_action":"BUY_NOW"},job)
        with self.assertRaises(ValueError):validate_analysis({**VALID,"instrument":"XAUUSD"},job)


class FailureModeTests(unittest.IsolatedAsyncioTestCase):
    async def _runtime(self,config=None):
        td=tempfile.TemporaryDirectory();runtime=FelineRuntime(AppConfig(database_path=str(Path(td.name)/"x.db"),ai=config or AIConfig(enabled=True,decision_mode="confirm_or_veto",minimum_confidence=.65)),ai_client=Client(VALID),recover=False);return td,runtime

    async def test_timeout_and_malformed_fail_safe(self):
        for client in (Client(VALID,delay=.05),Client({"bad":True})):
            outputs=[];worker=AIWorker(AIConfig(request_timeout_seconds=.005),client,outputs.append);worker.start();worker.submit_nowait(AnalysisJob(NewsEvent(headline="h",body="b",instruments=("EURUSD",))));await worker.queue.join();self.assertFalse(outputs[0].available);self.assertEqual(outputs[0].suggested_action,"NO_TRADE");await worker.stop()

    async def test_low_confidence_contradiction_stale_and_market_move_veto(self):
        cases=[{"confidence":.2},{"suggested_action":"SHORT"},{"expires_at":datetime.now(UTC)-timedelta(seconds=1)},{"move":True}]
        for case in cases:
            td,runtime=await self._runtime();now=datetime.now(UTC);sig=signal(now);runtime.broker.update_quote(PriceTick(timestamp=now,instrument="EURUSD",bid=1.0999,ask=1.1001));runtime.pending_ai["j"]=PendingAIAssessment(sig,1.1,now,now+timedelta(seconds=20))
            if case.pop("move",False):runtime.broker.update_quote(PriceTick(timestamp=now+timedelta(seconds=1),instrument="EURUSD",bid=1.109,ask=1.111))
            await runtime._handle_ai_result(result("j",now,**case));await runtime.bus.drain();self.assertTrue(runtime.latest_ai.vetoed);self.assertTrue(runtime.latest_ai.downstream_decision.startswith("NO_TRADE"));self.assertEqual(len(runtime.broker.fills),0);runtime.database.close();td.cleanup()

    async def test_confirmation_still_cannot_bypass_kill_switch(self):
        td,runtime=await self._runtime();now=datetime.now(UTC);sig=signal(now);runtime.broker.update_quote(PriceTick(timestamp=now,instrument="EURUSD",bid=1.0999,ask=1.1001));runtime.pending_ai["j"]=PendingAIAssessment(sig,1.1,now,now+timedelta(seconds=20));runtime.risk.activate_kill_switch();await runtime._handle_ai_result(result("j",now));await runtime.bus.drain();self.assertEqual(len(runtime.broker.fills),0);self.assertEqual(runtime.latest_ai.downstream_decision,"CONFIRMED:risk_rejected:kill_switch");runtime.database.close();td.cleanup()

    async def test_valid_confirmation_reaches_paper_broker_via_risk(self):
        td,runtime=await self._runtime();now=datetime.now(UTC);sig=signal(now);runtime.broker.update_quote(PriceTick(timestamp=now,instrument="EURUSD",bid=1.0999,ask=1.1001));runtime.pending_ai["j"]=PendingAIAssessment(sig,1.1,now,now+timedelta(seconds=20));await runtime._handle_ai_result(result("j",now));await runtime.bus.drain();self.assertTrue(runtime.latest_ai.downstream_decision.startswith("CONFIRMED"));self.assertGreater(len(runtime.broker.fills),0);self.assertGreaterEqual(runtime.database.count("risk_events"),1);runtime.database.close();td.cleanup()


if __name__=="__main__":unittest.main()
