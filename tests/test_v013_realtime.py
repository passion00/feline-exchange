from __future__ import annotations

import asyncio,tempfile,unittest
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from pathlib import Path

from feline.config import AIConfig,AppConfig,StrategyConfig
from feline.core.events import PriceTick
from feline.market.candles import CandleAggregator
from feline.market.datafeed import ProviderCapabilities,RealtimeDataProvider
from feline.market.realtime import RealtimeIngestionProvider,RealtimeSessionConfig
from feline.runtime import FelineRuntime

UTC=timezone.utc


class ScriptedSource(RealtimeDataProvider):
    capabilities=ProviderCapabilities("simulated",False,True,True,False,("EURUSD",))
    def __init__(self,scripts):self.scripts=list(scripts);self.calls=0
    async def stream(self,instruments):
        script=self.scripts[min(self.calls,len(self.scripts)-1)];self.calls+=1
        if isinstance(script,Exception):raise script
        for item in script:
            if isinstance(item,Exception):raise item
            yield item
        await asyncio.Future()


class RealtimeIngestionTests(unittest.IsolatedAsyncioTestCase):
    async def test_normalizes_ingestion_session_and_sequence(self):
        now=datetime.now(UTC);source=ScriptedSource([[PriceTick(timestamp=now,instrument="EURUSD",bid=1.1,ask=1.1001,source="simulated")]])
        provider=RealtimeIngestionProvider(source,RealtimeSessionConfig(stale_after_seconds=60,feed_timeout_seconds=1))
        tick=await anext(provider.stream());self.assertEqual(tick.realtime_session_id,provider.session_id);self.assertEqual(tick.provider_sequence,1);self.assertIsNotNone(tick.ingestion_timestamp)
        await provider.stop()

    async def test_reconnect_after_transient_failure(self):
        now=datetime.now(UTC);states=[];source=ScriptedSource([ConnectionError("lost"),[PriceTick(timestamp=now,instrument="EURUSD",bid=1,ask=1.1,source="simulated")]])
        async def health(event):states.append(event.state)
        provider=RealtimeIngestionProvider(source,RealtimeSessionConfig(stale_after_seconds=60,feed_timeout_seconds=.1,reconnect_delay_seconds=.001),health_callback=health)
        tick=await asyncio.wait_for(anext(provider.stream()),1);self.assertEqual(tick.bid,1);self.assertGreaterEqual(source.calls,2);self.assertIn("DEGRADED",states);self.assertEqual(states[-1],"HEALTHY")
        await provider.stop()

    async def test_feed_timeout_is_visible_and_emits_no_tick(self):
        states=[];source=ScriptedSource([[]])
        async def health(event):states.append(event.state)
        provider=RealtimeIngestionProvider(source,RealtimeSessionConfig(feed_timeout_seconds=.01,reconnect_delay_seconds=.001),health_callback=health)
        task=asyncio.create_task(anext(provider.stream()));await asyncio.sleep(.04);await provider.stop();task.cancel();await asyncio.gather(task,return_exceptions=True)
        self.assertIn("STALE",states);self.assertNotIn("HEALTHY",states)

    async def test_runtime_persists_session_quotes_and_completed_candle(self):
        now=datetime.now(UTC).replace(second=5,microsecond=0);prior=now.replace(second=10)-timedelta(minutes=1)
        ticks=[PriceTick(timestamp=prior,instrument="EURUSD",bid=1.1,ask=1.1002,source="simulated"),PriceTick(timestamp=now,instrument="EURUSD",bid=1.1001,ask=1.1003,source="simulated")]
        provider=RealtimeIngestionProvider(ScriptedSource([ticks]),RealtimeSessionConfig(stale_after_seconds=120,feed_timeout_seconds=1))
        with tempfile.TemporaryDirectory() as td:
            config=AppConfig(database_path=str(Path(td)/"live.db"),ai=AIConfig(enabled=False),strategy=StrategyConfig(enabled=False),snapshot_interval_ticks=1)
            runtime=FelineRuntime(config,provider=provider,recover=False);await runtime.run(.08);await runtime.stop()
            self.assertEqual(runtime.database.count("realtime_sessions"),1);self.assertEqual(runtime.database.count("realtime_quotes"),2);self.assertGreaterEqual(runtime.database.count("candles"),1)
            row=runtime.database.connection.execute("SELECT status,payload FROM realtime_sessions").fetchone();self.assertEqual(row["status"],"completed");runtime.database.close()

    async def test_degraded_feed_blocks_order_gate(self):
        now=datetime.now(UTC);provider=RealtimeIngestionProvider(ScriptedSource([[]]),RealtimeSessionConfig())
        with tempfile.TemporaryDirectory() as td:
            runtime=FelineRuntime(AppConfig(database_path=str(Path(td)/"x.db"),ai=AIConfig(enabled=False)),provider=provider,recover=False)
            from feline.core.events import OrderRequest,Side
            decision=await runtime.request_order(OrderRequest(timestamp=now,instrument="EURUSD",side=Side.BUY,quantity=1,expected_price=1.1,stop_price=1.09))
            self.assertFalse(decision.approved);self.assertEqual(decision.rule,"market_feed");await runtime.stop();runtime.database.close()


class CandleBoundaryTests(unittest.TestCase):
    def test_only_next_minute_completes_previous_candle(self):
        start=datetime(2024,1,1,12,tzinfo=UTC);aggregator=CandleAggregator(("1m",))
        self.assertEqual(aggregator.update(PriceTick(timestamp=start+timedelta(seconds=1),instrument="EURUSD",bid=1,ask=1.2)),[])
        self.assertEqual(aggregator.update(PriceTick(timestamp=start+timedelta(seconds=59),instrument="EURUSD",bid=1.1,ask=1.3)),[])
        completed=aggregator.update(PriceTick(timestamp=start+timedelta(minutes=1),instrument="EURUSD",bid=1.2,ask=1.4));self.assertEqual(len(completed),1);self.assertEqual(completed[0].close_time,start+timedelta(minutes=1));self.assertTrue(completed[0].complete)


if __name__=="__main__":unittest.main()
