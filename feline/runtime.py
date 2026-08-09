from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from feline.config import AppConfig
from feline.core.bus import EventBus
from feline.core.events import AIAnalysisResult, EmergencyEvent, NewsEvent, OrderRequest, PriceTick
from feline.execution.paper import PaperBroker
from feline.intelligence.service import AIWorker, AnalysisJob, LlamaCppClient
from feline.market.providers import MarketDataProvider, MockMarketDataProvider
from feline.quant.indicators import RollingReturns
from feline.risk.engine import RiskEngine
from feline.storage.database import Database


class FelineRuntime:
    def __init__(self, config: AppConfig, provider: MarketDataProvider | None = None, ai_client=None) -> None:
        self.config = config
        self.bus = EventBus()
        self.broker = PaperBroker(config.paper.initial_cash, config.paper.slippage_bps)
        self.risk = RiskEngine(config.risk)
        self.database = Database(Path(config.database_path))
        self.provider = provider or MockMarketDataProvider(config.tick_interval_seconds)
        self.indicators: dict[str, RollingReturns] = {}
        self.emergency_stop_path = Path("data/EMERGENCY_STOP")
        self.running = False
        self.ai = AIWorker(config.ai, ai_client or LlamaCppClient(config.ai), self.bus.publish)
        for event_type in (PriceTick, AIAnalysisResult, EmergencyEvent):
            self.bus.subscribe(event_type, self._persist)

    async def _persist(self, event) -> None:
        self.database.persist_event(event)

    async def handle_tick(self, tick: PriceTick) -> None:
        self.broker.update_quote(tick)
        indicator = self.indicators.setdefault(tick.instrument, RollingReturns())
        indicator.update(tick.mid)
        if self.risk.emergency_volatility(indicator.volatility):
            await self.bus.publish(EmergencyEvent(reason="Emergency volatility threshold exceeded", kill_switch_active=True))
        await self.bus.publish(tick)

    async def request_order(self, order: OrderRequest):
        decision = self.risk.approve_order(order, self.broker.get_quote(order.instrument), self.broker.get_positions())
        self.database.persist_event(decision)
        if not decision.approved:
            return decision
        update = await self.broker.submit_order(order)
        self.database.persist_event(update)
        return update

    def submit_news(self, event: NewsEvent) -> bool:
        self.database.persist_event(event)
        return self.ai.submit_nowait(AnalysisJob(event))

    async def run(self, duration: float | None = None) -> None:
        self.running = True
        self.ai.start()
        async def loop():
            async for tick in self.provider.stream():
                if not self.running:
                    break
                if self.emergency_stop_path.exists():
                    self.risk.activate_kill_switch()
                    await self.bus.publish(EmergencyEvent(reason="Operator emergency-stop marker detected", kill_switch_active=True))
                    self.running = False
                    break
                await self.handle_tick(tick)
        task = asyncio.create_task(loop())
        try:
            if duration is None:
                await task
            else:
                await asyncio.sleep(duration)
        finally:
            self.running = False
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await self.bus.drain()

    async def stop(self) -> None:
        self.running = False
        await self.ai.stop()
        await self.bus.drain()
