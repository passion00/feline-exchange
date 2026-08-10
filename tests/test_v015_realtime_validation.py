from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from feline.config import AIConfig, AppConfig, StrategyConfig
from feline.core.events import AIAnalysisResult, FeedHealthEvent, OrderRequest, PriceTick, Side, SignalEvent
from feline.market.datafeed import ProviderCapabilities, RealtimeDataProvider
from feline.market.realtime import RealtimeIngestionProvider, RealtimeSessionConfig
from feline.research.realtime_validation import RealtimeValidationTracker, compare_validation_sessions, export_validation_summary, load_validation_summary
from feline.runtime import FelineRuntime
from feline.portfolio.trades import ExitReason, TradeLifecycle

UTC = timezone.utc


class Source(RealtimeDataProvider):
    capabilities = ProviderCapabilities("validation_fixture", False, True, True, False, ("EURUSD",))
    def __init__(self, ticks): self.ticks = ticks
    async def stream(self, instruments):
        for tick in self.ticks: yield tick
        await asyncio.Future()


class TrackerTests(unittest.IsolatedAsyncioTestCase):
    async def test_ai_rates_latency_confidence_and_block_reasons(self):
        tracker = RealtimeValidationTracker("confirm_or_veto"); now = datetime.now(UTC)
        tracker.record_ai_request(); tracker.record_ai_request()
        await tracker.observe(SignalEvent(timestamp=now, instrument="EURUSD", side=Side.BUY, strength=.5, strategy="s", price=1.1))
        await tracker.observe(AIAnalysisResult(timestamp=now, job_id="1", instrument="EURUSD", event_type="market", direction="up", importance=.5, confidence=.8, time_horizon="minutes", summary="ok", evidence=(), latency_ms=20, suggested_action="LONG", downstream_decision="CONFIRMED:risk_approved"))
        await tracker.observe(AIAnalysisResult(timestamp=now, job_id="2", instrument="EURUSD", event_type="market", direction="neutral", importance=0, confidence=.2, time_horizon="minutes", summary="late", evidence=(), latency_ms=50, suggested_action="NO_TRADE", downstream_decision="NO_TRADE:stale_context", vetoed=True))
        metrics = tracker.ai_metrics()
        self.assertEqual(metrics["requests"], 2); self.assertEqual(metrics["approvals"], 1); self.assertEqual(metrics["vetoes"], 1)
        self.assertEqual(metrics["stale_response_rate"], .5); self.assertEqual(metrics["latency_ms"]["median"], 35)
        self.assertEqual(tracker.final_signals, 1); self.assertEqual(tracker.blocked_decisions["stale_context"], 1)

    async def test_runtime_exports_and_persists_validation_summary(self):
        start = datetime.now(UTC) - timedelta(seconds=1)
        ticks = [PriceTick(timestamp=start, instrument="EURUSD", bid=1.1, ask=1.1002, source="fixture"), PriceTick(timestamp=start + timedelta(milliseconds=100), instrument="EURUSD", bid=1.1001, ask=1.1003, source="fixture")]
        provider = RealtimeIngestionProvider(Source(ticks), RealtimeSessionConfig(stale_after_seconds=120, feed_timeout_seconds=1))
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); config = AppConfig(database_path=str(root / "feline.db"), ai=AIConfig(enabled=False), strategy=StrategyConfig(enabled=False), snapshot_interval_ticks=1)
            runtime = FelineRuntime(config, provider=provider, recover=False, validation_mode="deterministic", validation_output_root=root / "reports")
            await runtime.run(.05); await runtime.stop()
            self.assertIsNotNone(runtime.validation_summary); self.assertEqual(runtime.validation_summary["market"]["quotes"], 2)
            self.assertEqual(runtime.validation_summary["validation_status"], "WARN")
            self.assertEqual(runtime.database.count("realtime_validation_summaries"), 1)
            self.assertTrue((runtime.validation_output_directory / "summary.json").exists())
            runtime.database.close()

    async def test_protective_exit_closes_reporting_lifecycle(self):
        with tempfile.TemporaryDirectory() as td:
            runtime=FelineRuntime(AppConfig(database_path=str(Path(td)/"x.db"),ai=AIConfig(enabled=False)),recover=False)
            now=datetime.now(UTC);await runtime.handle_tick(PriceTick(timestamp=now,instrument="EURUSD",bid=1.1,ask=1.1002),build_candles=False)
            await runtime.request_order(OrderRequest(timestamp=now,instrument="EURUSD",side=Side.BUY,quantity=1,expected_price=1.1001,stop_price=1.099))
            self.assertIn("EURUSD",runtime.trades.open)
            await runtime.handle_tick(PriceTick(timestamp=now+timedelta(seconds=1),instrument="EURUSD",bid=1.0988,ask=1.099),build_candles=False)
            self.assertNotIn("EURUSD",runtime.trades.open);self.assertEqual(len(runtime.trades.completed),1);self.assertEqual(runtime.trades.completed[0].exit_reason,ExitReason.STOP);self.assertEqual(runtime.database.count("trades"),1)
            await runtime.bus.drain();runtime.database.close()


class ComparisonTests(unittest.TestCase):
    def test_trade_lifecycle_cost_attribution_does_not_double_count_fill_friction(self):
        lifecycle=TradeLifecycle();now=datetime.now(UTC);trade=lifecycle.start("EURUSD","long","s","1",None,now,2,100)
        trade.spread_cost=1;trade.slippage_cost=2
        closed=lifecycle.close("EURUSD",now+timedelta(minutes=1),105,ExitReason.TARGET,costs=.5,spread_cost=1,slippage_cost=2)
        self.assertEqual(closed.gross_pnl,10);self.assertEqual(closed.net_pnl,9.5);self.assertEqual(closed.spread_cost,2);self.assertEqual(closed.slippage_cost,4)

    def summary(self, sid, mode, start="2026-01-01T00:00:00+00:00", trades=40, pnl=1.0):
        return {"schema_version":"1.0","validation_session_id":sid,"realtime_session_id":sid,"validation_mode":mode,"provider":"fixture","instruments":["EURUSD"],"started_at":start,"ended_at":"2026-01-01T01:00:00+00:00","created_at":start,"paper_only":True,"configuration":{"ai":{"decision_mode":mode},"strategy":{},"risk":{},"paper":{}},"configuration_checksum":"x","market":{"quotes":100,"last_source_timestamp":None,"feed_states":{}},"signals":{"deterministic_before_ai":10,"final_after_ai":8,"blocked":2,"blocked_reasons":{}},"ai":{"requests":10,"responses":10,"available_responses":10,"errors":0,"timeout_or_error_rate":0,"stale_responses":0,"stale_response_rate":0,"approvals":8,"vetoes":2,"approval_rate":.8,"veto_rate":.2,"latency_ms":{},"confidence":{},"errors_by_type":{},"decisions":{},"queue_dropped":0},"risk":{"blocked":0,"blocked_reasons":{}},"execution":{"orders":trades,"fills":trades*2,"costs":{},"total_costs":0},"performance":{"trades":trades,"net_pnl":pnl,"win_rate":.5,"max_drawdown":.2},"validation_status":"PASS","warnings":[]}

    def test_exact_period_comparison_and_machine_exports(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); a=self.summary("a","deterministic",pnl=1); b=self.summary("b","confirm_or_veto",pnl=2)
            pa=export_validation_summary(a,root);pb=export_validation_summary(b,root)
            result=compare_validation_sessions([pa,pb],root/"comparison")
            self.assertTrue(result["equivalent_market_periods"]);self.assertEqual(result["ai_effect"],"descriptively_helping")
            self.assertTrue((root/"comparison"/"comparison.csv").exists());self.assertEqual(load_validation_summary(pa)["validation_session_id"],"a")

    def test_non_equivalent_or_small_samples_are_insufficient(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);a=self.summary("a","deterministic",trades=2);b=self.summary("b","advisory",start="2026-01-02T00:00:00+00:00",trades=2)
            pa=export_validation_summary(a,root);pb=export_validation_summary(b,root);result=compare_validation_sessions([pa,pb])
            self.assertFalse(result["equivalent_market_periods"]);self.assertEqual(result["ai_effect"],"insufficient_evidence");self.assertEqual(result["validation_status"],"WARN")


if __name__ == "__main__": unittest.main()
