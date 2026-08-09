from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from feline.config import AppConfig
from feline.core.bus import EventBus
from feline.core.events import AIAnalysisResult, CandleUpdate, EconomicEvent, EmergencyEvent,FillEvent,FinancingEvent, NewsEvent, OrderRequest, PortfolioSnapshot, PriceTick, Regime, RegimeEvent, RiskEvent, SignalEvent
from feline.execution.paper import PaperBroker
from feline.intelligence.service import AIWorker, AnalysisJob, LlamaCppClient
from feline.market.providers import MarketDataProvider, MockMarketDataProvider
from feline.market.candles import CandleAggregator
from feline.news.pipeline import NewsPipeline
from feline.quant.framework import IndicatorState
from feline.quant.indicators import RollingReturns
from feline.quant.regime import RegimeDetector
from feline.risk.engine import RiskEngine
from feline.storage.database import Database
from feline.strategy.reference import ReferenceStrategy
from feline.portfolio.allocator import PortfolioAllocator
from feline.portfolio.trades import ExitReason,TradeLifecycle


class FelineRuntime:
    def __init__(self, config: AppConfig, provider: MarketDataProvider | None = None, ai_client=None, recover: bool = True, replay_session_id: str | None = None) -> None:
        self.config = config
        self.replay_session_id = replay_session_id
        self.bus = EventBus()
        self.broker = PaperBroker(config=config.paper)
        self.risk = RiskEngine(config.risk)
        self.database = Database(Path(config.database_path))
        self.provider = provider or MockMarketDataProvider(config.tick_interval_seconds)
        self.indicators: dict[str, RollingReturns] = {}
        self.quant: dict[str, IndicatorState] = {}
        self.candles = CandleAggregator()
        self.regimes = RegimeDetector()
        self.strategy = ReferenceStrategy(config.strategy)
        self.allocator=PortfolioAllocator()
        self.trades=TradeLifecycle()
        self.news_pipeline = NewsPipeline()
        self.tick_count = 0
        self.last_market_timestamp = None
        self.peak_equity = config.paper.initial_cash
        self.equity_history: list[float] = []
        self.trade_pnls: list[float] = []
        self._persisted_fill_count=0
        self.exposure_samples = 0
        self.emergency_stop_path = Path("data/EMERGENCY_STOP")
        self.running = False
        self.ai = AIWorker(config.ai, ai_client or LlamaCppClient(config.ai), self.bus.publish)
        recovered = self.database.recover_broker_state() or self.database.latest_portfolio()
        if recovered and recover: self.broker.restore(*recovered)
        self.database.save_health("database","ok",{})
        self.database.save_health("event_bus","ok",{"pending":0})
        self.database.save_health("paper_broker","paper_only",{})
        self.database.save_health("news_provider","not_configured",{})
        self.database.save_health("economic_calendar","not_configured",{})
        self.database.save_health("ai","unknown",{"queue_depth":0})
        for event_type in (PriceTick, CandleUpdate, RegimeEvent, SignalEvent, RiskEvent, AIAnalysisResult, EmergencyEvent):
            self.bus.subscribe(event_type, self._persist)

    async def _persist(self, event) -> None:
        if self.replay_session_id and getattr(event,"replay_session_id",None) is None:event=__import__('dataclasses').replace(event,replay_session_id=self.replay_session_id)
        self.database.persist_event(event)
        if isinstance(event,AIAnalysisResult):self.database.save_health("ai","available" if event.available else "unavailable",{"queue_depth":self.ai.queue.qsize(),"error":event.error})

    async def handle_tick(self, tick: PriceTick) -> None:
        if self.replay_session_id and tick.replay_session_id != self.replay_session_id:
            tick = __import__('dataclasses').replace(tick,replay_session_id=self.replay_session_id)
        self.last_market_timestamp=tick.timestamp
        self.trades.update(tick.instrument,tick.bid,tick.ask)
        before_realized=sum(p.realized_pnl for p in self.broker.positions.values())
        protective_updates=await self.broker.process_quote(tick)
        new_fills=self.broker.fills[self._persisted_fill_count:]
        if protective_updates or new_fills:self.database.commit_execution(self.broker,new_fills,protective_updates)
        self._persisted_fill_count=len(self.broker.fills)
        after_realized=sum(p.realized_pnl for p in self.broker.positions.values())
        if after_realized != before_realized:self.trade_pnls.append(after_realized-before_realized)
        indicator = self.indicators.setdefault(tick.instrument, RollingReturns())
        indicator.update(tick.mid)
        if self.risk.emergency_volatility(indicator.volatility):
            await self.bus.publish(EmergencyEvent(reason="Emergency volatility threshold exceeded", kill_switch_active=True))
        state=self.quant.setdefault(tick.instrument,IndicatorState())
        state.update(tick.mid,tick.mid,tick.mid,tick.timestamp.timestamp())
        transition=self.regimes.update(tick.instrument,samples=len(state.closes),momentum=state.momentum(3),volatility=state.volatility(4),spread=tick.spread_ratio)
        if transition:
            await self.bus.publish(transition)
        regime=self.regimes.current.get(tick.instrument,Regime.INSUFFICIENT_DATA)
        self.broker.extreme_volatility=regime is Regime.EXTREME_VOLATILITY
        for candle in self.candles.update(tick):
            await self.bus.publish(candle)
            signal=self.strategy.on_candle(candle,regime)
            if signal and self.config.strategy.enabled:
                await self.bus.publish(signal)
                portfolio=self.broker.portfolio_state(); quantity,stop,target=self.strategy.order_from_signal(signal,portfolio["equity"])
                allocated=self.allocator.allocate(signal,portfolio["equity"],portfolio["cash"],portfolio["exposure"]);quantity=min(quantity,allocated)
                order=OrderRequest(instrument=signal.instrument,side=signal.side,quantity=quantity,expected_price=signal.price,stop_price=stop,take_profit_price=target,signal_id=signal.id,correlation_id=signal.id,timestamp=signal.timestamp,replay_session_id=self.replay_session_id)
                await self.request_order(order)
        self.tick_count+=1
        portfolio=self.broker.portfolio_state(); self.peak_equity=max(self.peak_equity,portfolio["equity"]); self.equity_history.append(portfolio["equity"])
        if portfolio["exposure"]>0:self.exposure_samples+=1
        self.risk.update_account(daily_pnl=portfolio["realized_pnl"],equity=portfolio["equity"],peak_equity=self.peak_equity)
        if self.tick_count % self.config.snapshot_interval_ticks==0:self.snapshot()
        self.database.save_health("market_provider","connected",{"last_tick":tick.timestamp.isoformat(),"source":tick.source})
        self.database.save_health("risk","kill_switch" if self.risk.kill_switch else "ok",{"danger_mode":self.risk.danger.active(tick.timestamp)})
        await self.bus.publish(tick)

    async def request_order(self, order: OrderRequest):
        decision = self.risk.approve_order(order, self.broker.get_quote(order.instrument), self.broker.get_positions())
        await self.bus.publish(decision)
        if not decision.approved:
            return decision
        before=sum(p.realized_pnl for p in self.broker.positions.values());before_quantity=self.broker.positions.get(order.instrument).quantity if order.instrument in self.broker.positions else 0
        update = await self.broker.submit_order(order)
        new_fills=self.broker.fills[self._persisted_fill_count:]
        after=sum(p.realized_pnl for p in self.broker.positions.values())
        after_quantity=self.broker.positions.get(order.instrument).quantity if order.instrument in self.broker.positions else 0
        if before_quantity==0 and after_quantity!=0 and new_fills:self.trades.start(order.instrument,"long" if after_quantity>0 else "short","reference",ReferenceStrategy.VERSION,order.signal_id,new_fills[0].timestamp,abs(after_quantity),new_fills[0].fill_price)
        elif before_quantity!=0 and after_quantity==0 and order.instrument in self.trades.open and new_fills:self.trades.close(order.instrument,new_fills[-1].timestamp,new_fills[-1].fill_price,ExitReason.STRATEGY,sum(f.commission+f.spread_cost+f.slippage_amount for f in new_fills))
        if after != before:self.trade_pnls.append(after-before)
        self.database.commit_execution(self.broker,new_fills,[update])
        self._persisted_fill_count=len(self.broker.fills)
        return update

    def submit_news(self, event: NewsEvent) -> bool:
        normalized=self.news_pipeline.process(event)
        if normalized is None:return False
        self.database.persist_event(normalized.event)
        self.database.save_health("news_provider","connected",{"last_news":event.timestamp.isoformat()})
        return self.ai.submit_nowait(AnalysisJob(normalized.event,priority=normalized.priority))

    def schedule_economic_event(self,event:EconomicEvent)->None:
        self.risk.danger.schedule(event); self.database.persist_event(event);self.database.save_health("economic_calendar","connected",{"last_event":event.name})

    def snapshot(self) -> PortfolioSnapshot:
        state=self.broker.portfolio_state(); drawdown=(self.peak_equity-state["equity"])/self.peak_equity if self.peak_equity else 0
        event=PortfolioSnapshot(timestamp=self.last_market_timestamp or __import__('datetime').datetime.now(__import__('datetime').timezone.utc),replay_session_id=self.replay_session_id,cash=state["cash"],equity=state["equity"],realized_pnl=state["realized_pnl"],unrealized_pnl=state["unrealized_pnl"],exposure=state["exposure"],peak_equity=self.peak_equity,drawdown=drawdown,trading_state="kill_switch" if self.risk.kill_switch else "enabled",positions={key:{"quantity":p.quantity,"average_price":p.average_price,"realized_pnl":p.realized_pnl} for key,p in self.broker.positions.items()})
        self.database.persist_event(event);return event

    async def run(self, duration: float | None = None) -> None:
        self.running = True
        self.ai.start()
        async def loop():
            try:
                async for tick in self.provider.stream():
                    if not self.running:
                        break
                    if self.emergency_stop_path.exists():
                        self.risk.activate_kill_switch()
                        await self.bus.publish(EmergencyEvent(reason="Operator emergency-stop marker detected", kill_switch_active=True))
                        self.running = False
                        break
                    await self.handle_tick(tick)
            except Exception as exc:
                self.database.save_health("market_provider","failed",{"error":type(exc).__name__})
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
            for candle in self.candles.flush(): self.database.persist_event(candle)
            self.snapshot()
            self.database.persist_broker_state(self.broker)
            self.database.save_health("market_provider","disconnected",{})

    async def stop(self) -> None:
        self.running = False
        await self.ai.stop()
        await self.bus.drain()
        ai_status="not_checked" if self.ai.last_available is None else "available" if self.ai.last_available else "unavailable"
        if self.ai.dropped:ai_status="pressure"
        self.database.save_health("ai",ai_status,{"queue_depth":self.ai.queue.qsize(),"dropped":self.ai.dropped})
        self.database.save_health("event_bus","ok",{"pending":len(self.bus._tasks)})
        self.database.save_health("paper_broker","ok",{"positions":len(self.broker.positions)})

    async def finalize_replay(self,policy:str|None=None)->str:
        policy=(policy or self.config.paper.replay_end_policy).upper()
        if policy=="FORCE_CLOSE":
            for instrument,p in list(self.broker.positions.items()):
                if p.quantity:await self.broker.close_position(instrument)
            for fill in self.broker.fills[self._persisted_fill_count:]:self.database.persist_event(fill)
            self._persisted_fill_count=len(self.broker.fills);self.snapshot();self.database.persist_broker_state(self.broker)
        elif policy not in {"MARK_TO_MARKET","LEAVE_OPEN"}:raise ValueError("invalid replay end policy")
        return policy
