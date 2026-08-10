from __future__ import annotations

import asyncio
import logging
from collections import defaultdict,deque
from dataclasses import dataclass,replace
from datetime import datetime,timedelta,timezone
from pathlib import Path

from feline.config import AppConfig
from feline.core.bus import EventBus
from feline.core.events import AIAnalysisResult, CandleUpdate, EconomicEvent, EmergencyEvent,FeedHealthEvent,FillEvent,FinancingEvent, NewsEvent, OrderRequest, PortfolioSnapshot, PriceTick, Regime, RegimeEvent, RiskEvent, SignalEvent
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


@dataclass(frozen=True)
class PendingAIAssessment:
    signal: SignalEvent
    reference_price: float
    context_timestamp: datetime
    expires_at: datetime


class FelineRuntime:
    def __init__(self, config: AppConfig, provider: MarketDataProvider | None = None, ai_client=None, recover: bool = True, replay_session_id: str | None = None, validation_mode: str | None = None, validation_output_root: Path | None = None) -> None:
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
        self.first_market_timestamp = None
        self.peak_equity = config.paper.initial_cash
        self.equity_history: list[float] = []
        self.trade_pnls: list[float] = []
        self._persisted_fill_count=0
        self.exposure_samples = 0
        self.emergency_stop_path = Path("data/EMERGENCY_STOP")
        self.running = False
        self.realtime_session_id=getattr(self.provider,"session_id",None);self.feed_trading_ready=not bool(self.realtime_session_id);self.feed_state="OFF"
        if self.realtime_session_id:self.provider.health_callback=self._feed_health
        self.recent_candles=defaultdict(lambda:deque(maxlen=20));self.recent_signals=deque(maxlen=20);self.recent_news=deque(maxlen=20);self.pending_ai:dict[str,PendingAIAssessment]={};self.latest_ai=None
        self.validation_mode=validation_mode;self.validation_output_root=validation_output_root or Path("data/reports/realtime_validation");self.validation_tracker=None;self.validation_summary=None;self.validation_output_directory=None;self._realtime_session_record=None
        if validation_mode:
            from feline.research.realtime_validation import RealtimeValidationTracker
            self.validation_tracker=RealtimeValidationTracker(validation_mode)
        self.ai = AIWorker(config.ai, ai_client or LlamaCppClient(config.ai), self._handle_ai_result)
        recovered = self.database.recover_broker_state() or self.database.latest_portfolio()
        if recovered and recover: self.broker.restore(*recovered)
        self.database.save_health("database","ok",{})
        self.database.save_health("event_bus","ok",{"pending":0})
        self.database.save_health("paper_broker","paper_only",{})
        self.database.save_health("news_provider","not_configured",{})
        self.database.save_health("economic_calendar","not_configured",{})
        self.database.save_health("ai","unknown",{"queue_depth":0})
        for event_type in (PriceTick, CandleUpdate, RegimeEvent, SignalEvent, RiskEvent, AIAnalysisResult, EmergencyEvent,FeedHealthEvent):
            self.bus.subscribe(event_type, self._persist)
            if self.validation_tracker:self.bus.subscribe(event_type,self.validation_tracker.observe)

    async def _feed_health(self,event:FeedHealthEvent)->None:
        self.feed_state=event.state;self.feed_trading_ready=event.state=="HEALTHY"
        self.database.save_health("market_provider",event.state.lower(),{"provider":event.provider,"session":event.realtime_session_id,"last_source_timestamp":event.last_source_timestamp.isoformat() if event.last_source_timestamp else None,"last_ingestion_timestamp":event.last_ingestion_timestamp.isoformat() if event.last_ingestion_timestamp else None,"message":event.message})
        await self.bus.publish(event)

    def _market_context(self,instrument:str,signal:SignalEvent|None=None)->dict:
        quote=self.broker.get_quote(instrument);portfolio=self.broker.portfolio_state();indicator=self.indicators.get(instrument);regime=self.regimes.current.get(instrument,Regime.INSUFFICIENT_DATA)
        return {"schema":"market-context-v1","instrument":instrument,"context_timestamp":(signal.timestamp if signal else self.last_market_timestamp or datetime.now(timezone.utc)).isoformat(),"price":{"bid":quote.bid,"ask":quote.ask,"mid":quote.mid,"spread_ratio":quote.spread_ratio} if quote else None,"candles":[{"open_time":x.open_time.isoformat(),"close_time":x.close_time.isoformat(),"open":x.open,"high":x.high,"low":x.low,"close":x.close,"timeframe":x.timeframe,"complete":x.complete} for x in self.recent_candles[instrument]],"indicators":dict(signal.indicators) if signal else {},"regime":regime.value,"volatility":indicator.volatility if indicator else None,"portfolio":{"equity":portfolio["equity"],"exposure":portfolio["exposure"],"positions":{k:{"quantity":v.quantity,"average_price":v.average_price} for k,v in self.broker.positions.items()}},"recent_signals":[{"instrument":x.instrument,"side":x.side.value,"strength":x.strength,"timestamp":x.timestamp.isoformat()} for x in self.recent_signals if x.instrument==instrument],"macro_news":[{"headline":x.headline,"source":x.source,"timestamp":x.timestamp.isoformat()} for x in self.recent_news if not x.instruments or instrument in x.instruments],"deterministic_signal":{"id":signal.id,"side":signal.side.value,"strength":signal.strength,"reason":signal.reason} if signal else None,"feed_state":self.feed_state}

    def _submit_signal_assessment(self,signal:SignalEvent)->bool:
        now=signal.timestamp;expires=now+timedelta(seconds=self.config.ai.context_max_age_seconds);context=self._market_context(signal.instrument,signal);event=NewsEvent(timestamp=now,headline="Evaluate deterministic paper signal",body="No external news; use only structured context.",source="feline_market_context",instruments=(signal.instrument,),correlation_id=signal.id);job=AnalysisJob(event,purpose="signal_assessment",signal_id=signal.id,context=context,context_timestamp=now,expires_at=expires)
        if self.validation_tracker:self.validation_tracker.record_ai_request()
        submitted=self.ai.submit_nowait(job)
        if submitted and self.config.ai.decision_mode=="confirm_or_veto":self.pending_ai[job.id]=PendingAIAssessment(signal,signal.price,now,expires)
        return submitted

    async def _handle_ai_result(self,result:AIAnalysisResult)->None:
        pending=self.pending_ai.pop(result.job_id,None);decision="advisory_only";vetoed=False
        if pending:
            now=datetime.now(timezone.utc);quote=self.broker.get_quote(pending.signal.instrument);expected="LONG" if pending.signal.side.value=="buy" else "SHORT";reason=None
            if not result.available:reason="unavailable"
            elif result.confidence<self.config.ai.minimum_confidence:reason="low_confidence"
            elif now>pending.expires_at or (result.expires_at and now>result.expires_at):reason="stale_context"
            elif quote is None:reason="missing_quote"
            elif abs(quote.mid/pending.reference_price-1)>self.config.ai.maximum_price_move_fraction:reason="market_moved"
            elif result.suggested_action!=expected:reason="contradictory_or_no_trade"
            if reason:decision=f"NO_TRADE:{reason}";vetoed=True
            else:
                outcome=await self._execute_signal(pending.signal)
                decision="CONFIRMED:risk_approved" if not isinstance(outcome,RiskEvent) or outcome.approved else f"CONFIRMED:risk_rejected:{outcome.rule}"
        result=replace(result,downstream_decision=decision,vetoed=vetoed,realtime_session_id=self.realtime_session_id);self.latest_ai=result;await self.bus.publish(result)

    async def _persist(self, event) -> None:
        if self.replay_session_id and getattr(event,"replay_session_id",None) is None:event=__import__('dataclasses').replace(event,replay_session_id=self.replay_session_id)
        self.database.persist_event(event)
        if isinstance(event,AIAnalysisResult):
            self.database.save_health("ai","available" if event.available else "unavailable",{"provider":event.provider,"model":event.model_version or event.model_identifier,"queue_depth":self.ai.queue.qsize(),"latency_ms":event.latency_ms,"suggested_action":event.suggested_action,"confidence":event.confidence,"downstream_decision":event.downstream_decision,"vetoed":event.vetoed,"error":event.error})

    async def handle_tick(self, tick: PriceTick, build_candles: bool = True) -> None:
        build_candles = build_candles and not tick.source.endswith(":synthetic_execution")
        if self.replay_session_id and tick.replay_session_id != self.replay_session_id:
            tick = __import__('dataclasses').replace(tick,replay_session_id=self.replay_session_id)
        self.last_market_timestamp=tick.timestamp
        if self.first_market_timestamp is None:self.first_market_timestamp=tick.timestamp
        if tick.realtime_session_id:self.database.save_realtime_quote(tick)
        self.trades.update(tick.instrument,tick.bid,tick.ask)
        position_before=self.broker.positions.get(tick.instrument);before_quantity=position_before.quantity if position_before else 0;protection_before=self.broker.protective.get(tick.instrument,(None,None))
        before_realized=sum(p.realized_pnl for p in self.broker.positions.values())
        protective_updates=await self.broker.process_quote(tick)
        new_fills=self.broker.fills[self._persisted_fill_count:]
        if protective_updates or new_fills:self.database.commit_execution(self.broker,new_fills,protective_updates)
        self._persisted_fill_count=len(self.broker.fills)
        after_realized=sum(p.realized_pnl for p in self.broker.positions.values())
        position_after=self.broker.positions.get(tick.instrument);after_quantity=position_after.quantity if position_after else 0
        if before_quantity and not after_quantity and tick.instrument in self.trades.open and new_fills:
            stop,target=protection_before;reason=ExitReason.STOP if stop is not None and (tick.bid<=stop if before_quantity>0 else tick.ask>=stop) else ExitReason.TARGET if target is not None and (tick.bid>=target if before_quantity>0 else tick.ask<=target) else ExitReason.STRATEGY
            trade=self.trades.close(tick.instrument,new_fills[-1].timestamp,new_fills[-1].fill_price,reason,sum(f.commission for f in new_fills),sum(f.spread_cost for f in new_fills),sum(f.slippage_amount for f in new_fills));self.database.save_trade(trade,"completed")
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
        for candle in self.candles.update(tick) if build_candles else ():
            await self.bus.publish(candle)
            self.recent_candles[candle.instrument].append(candle)
            signal=self.strategy.on_candle(candle,regime)
            if signal and self.config.strategy.enabled:
                if self.realtime_session_id:signal=replace(signal,realtime_session_id=self.realtime_session_id)
                self.recent_signals.append(signal);await self.bus.publish(signal);submitted=self._submit_signal_assessment(signal) if self.config.ai.enabled and self.config.ai.decision_mode in {"confirm_or_veto","record"} else False
                if self.config.ai.decision_mode!="confirm_or_veto":await self._execute_signal(signal)
                elif not submitted:
                    unavailable=AIAnalysisResult(job_id="queue-rejected:"+signal.id,instrument=signal.instrument,event_type="market_signal",direction="neutral",importance=0,confidence=0,time_horizon="none",summary="AI queue unavailable",reasoning_summary="Fail-safe NO_TRADE",evidence=(),available=False,error="QueueUnavailable",suggested_action="NO_TRADE",affected_signal_id=signal.id,downstream_decision="NO_TRADE:queue_unavailable",vetoed=True,realtime_session_id=self.realtime_session_id);self.latest_ai=unavailable;await self.bus.publish(unavailable)
        self.tick_count+=1
        portfolio=self.broker.portfolio_state(); self.peak_equity=max(self.peak_equity,portfolio["equity"]); self.equity_history.append(portfolio["equity"])
        if portfolio["exposure"]>0:self.exposure_samples+=1
        self.risk.update_account(daily_pnl=portfolio["realized_pnl"],equity=portfolio["equity"],peak_equity=self.peak_equity)
        if self.tick_count % self.config.snapshot_interval_ticks==0:self.snapshot()
        self.database.save_health("market_provider","healthy" if tick.realtime_session_id else "connected",{"last_tick":tick.timestamp.isoformat(),"ingestion_timestamp":tick.ingestion_timestamp.isoformat() if tick.ingestion_timestamp else None,"source":tick.source,"realtime_session_id":tick.realtime_session_id})
        self.database.save_health("risk","kill_switch" if self.risk.kill_switch else "ok",{"danger_mode":self.risk.danger.active(tick.timestamp)})
        await self.bus.publish(tick)

    async def _execute_signal(self,signal:SignalEvent):
        portfolio=self.broker.portfolio_state();quantity,stop,target=self.strategy.order_from_signal(signal,portfolio["equity"]);allocated=self.allocator.allocate(signal,portfolio["equity"],portfolio["cash"],portfolio["exposure"]);quantity=min(quantity,allocated);order=OrderRequest(instrument=signal.instrument,side=signal.side,quantity=quantity,expected_price=signal.price,stop_price=stop,take_profit_price=target,signal_id=signal.id,correlation_id=signal.id,timestamp=signal.timestamp,replay_session_id=self.replay_session_id);return await self.request_order(order)

    async def request_order(self, order: OrderRequest):
        if self.realtime_session_id and not self.feed_trading_ready:
            decision=RiskEvent(approved=False,rule="market_feed",message=f"Realtime feed is {self.feed_state}; stale/disconnected data cannot create orders",severity="high",order_request_id=order.id,correlation_id=order.correlation_id)
            await self.bus.publish(decision);return decision
        decision = self.risk.approve_order(order, self.broker.get_quote(order.instrument), self.broker.get_positions())
        await self.bus.publish(decision)
        if not decision.approved:
            return decision
        before=sum(p.realized_pnl for p in self.broker.positions.values());before_quantity=self.broker.positions.get(order.instrument).quantity if order.instrument in self.broker.positions else 0
        update = await self.broker.submit_order(order)
        new_fills=self.broker.fills[self._persisted_fill_count:]
        after=sum(p.realized_pnl for p in self.broker.positions.values())
        after_quantity=self.broker.positions.get(order.instrument).quantity if order.instrument in self.broker.positions else 0
        if before_quantity==0 and after_quantity!=0 and new_fills:
            trade=self.trades.start(order.instrument,"long" if after_quantity>0 else "short","reference",ReferenceStrategy.VERSION,order.signal_id,new_fills[0].timestamp,abs(after_quantity),new_fills[0].fill_price,self.realtime_session_id);trade.commissions=sum(f.commission for f in new_fills);trade.spread_cost=sum(f.spread_cost for f in new_fills);trade.slippage_cost=sum(f.slippage_amount for f in new_fills);self.database.save_trade(trade,"open")
        elif before_quantity!=0 and after_quantity==0 and order.instrument in self.trades.open and new_fills:
            trade=self.trades.close(order.instrument,new_fills[-1].timestamp,new_fills[-1].fill_price,ExitReason.STRATEGY,sum(f.commission for f in new_fills),sum(f.spread_cost for f in new_fills),sum(f.slippage_amount for f in new_fills));self.database.save_trade(trade,"completed")
        if after != before:self.trade_pnls.append(after-before)
        self.database.commit_execution(self.broker,new_fills,[update])
        self._persisted_fill_count=len(self.broker.fills)
        return update

    def submit_news(self, event: NewsEvent) -> bool:
        normalized=self.news_pipeline.process(event)
        if normalized is None:return False
        self.database.persist_event(normalized.event)
        self.database.save_health("news_provider","connected",{"last_news":event.timestamp.isoformat()})
        self.recent_news.append(normalized.event);instrument=normalized.event.instruments[0] if normalized.event.instruments else "UNKNOWN";context=self._market_context(instrument) if instrument!="UNKNOWN" else {"schema":"market-context-v1","instrument":"UNKNOWN","news_only":True};return self.ai.submit_nowait(AnalysisJob(normalized.event,priority=normalized.priority,context=context,purpose="news_assessment",context_timestamp=self.last_market_timestamp or datetime.now(timezone.utc),expires_at=datetime.now(timezone.utc)+timedelta(seconds=self.config.ai.context_max_age_seconds)))

    def schedule_economic_event(self,event:EconomicEvent)->None:
        self.risk.danger.schedule(event); self.database.persist_event(event);self.database.save_health("economic_calendar","connected",{"last_event":event.name})

    def snapshot(self) -> PortfolioSnapshot:
        state=self.broker.portfolio_state(); drawdown=(self.peak_equity-state["equity"])/self.peak_equity if self.peak_equity else 0
        event=PortfolioSnapshot(timestamp=self.last_market_timestamp or __import__('datetime').datetime.now(__import__('datetime').timezone.utc),replay_session_id=self.replay_session_id,cash=state["cash"],equity=state["equity"],realized_pnl=state["realized_pnl"],unrealized_pnl=state["unrealized_pnl"],exposure=state["exposure"],peak_equity=self.peak_equity,drawdown=drawdown,trading_state="kill_switch" if self.risk.kill_switch else "enabled",positions={key:{"quantity":p.quantity,"average_price":p.average_price,"realized_pnl":p.realized_pnl} for key,p in self.broker.positions.items()})
        self.database.persist_event(event);return event

    async def run(self, duration: float | None = None) -> None:
        self.running = True
        realtime_stop_path=Path("data/REALTIME_STOP")
        if self.realtime_session_id:realtime_stop_path.unlink(missing_ok=True)
        self.ai.start()
        realtime_session=None
        if self.realtime_session_id:
            now=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat();realtime_session={"realtime_session_id":self.realtime_session_id,"provider":self.provider.source.capabilities.provider,"instruments":list(self.provider.config.instruments),"started_at":now,"ended_at":None,"status":"running","feline_version":__import__('feline').__version__,"mode":"realtime_paper","validation_mode":self.validation_mode,"paper_only":True};self._realtime_session_record=realtime_session;self.database.save_realtime_session(realtime_session,"running")
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
        async def monitor_stop():
            while self.running and self.realtime_session_id:
                if realtime_stop_path.exists():
                    self.running=False;stop=getattr(self.provider,"stop",None)
                    if stop:await stop()
                    break
                await asyncio.sleep(.5)
        monitor=asyncio.create_task(monitor_stop()) if self.realtime_session_id else None
        try:
            if duration is None:
                await task
            else:
                await asyncio.sleep(duration)
        finally:
            self.running = False
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if monitor:monitor.cancel();await asyncio.gather(monitor,return_exceptions=True)
            await self.bus.drain()
            for candle in self.candles.flush(): self.database.persist_event(candle)
            self.snapshot()
            self.database.persist_broker_state(self.broker)
            self.database.save_health("market_provider","disconnected",{})
            if realtime_session:
                realtime_session.update({"ended_at":__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),"status":"completed","quotes":self.tick_count,"first_source_timestamp":self.first_market_timestamp.isoformat() if self.first_market_timestamp else None,"last_source_timestamp":self.last_market_timestamp.isoformat() if self.last_market_timestamp else None});self.database.save_realtime_session(realtime_session,"completed")

    async def stop(self) -> None:
        self.running = False
        stop=getattr(self.provider,"stop",None)
        if stop:await stop()
        await self.ai.stop()
        await self.bus.drain()
        ai_status="not_checked" if self.ai.last_available is None else "available" if self.ai.last_available else "unavailable"
        if self.ai.dropped:ai_status="pressure"
        self.database.save_health("ai",ai_status,{"queue_depth":self.ai.queue.qsize(),"dropped":self.ai.dropped})
        self.database.save_health("event_bus","ok",{"pending":len(self.bus._tasks)})
        self.database.save_health("paper_broker","ok",{"positions":len(self.broker.positions)})
        if self.validation_tracker and self._realtime_session_record and self.validation_summary is None:
            from feline.research.realtime_validation import build_validation_summary,export_validation_summary
            self.validation_summary=build_validation_summary(self,self._realtime_session_record,self.validation_tracker);self.database.save_realtime_validation(self.validation_summary);self.validation_output_directory=export_validation_summary(self.validation_summary,self.validation_output_root)

    async def finalize_replay(self,policy:str|None=None)->str:
        policy=(policy or self.config.paper.replay_end_policy).upper()
        if policy=="FORCE_CLOSE":
            for instrument,p in list(self.broker.positions.items()):
                if p.quantity:await self.broker.close_position(instrument)
            for fill in self.broker.fills[self._persisted_fill_count:]:self.database.persist_event(fill)
            self._persisted_fill_count=len(self.broker.fills);self.snapshot();self.database.persist_broker_state(self.broker)
        elif policy not in {"MARK_TO_MARKET","LEAVE_OPEN"}:raise ValueError("invalid replay end policy")
        return policy
