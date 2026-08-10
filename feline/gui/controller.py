from __future__ import annotations
from collections import deque
from dataclasses import dataclass,asdict,replace
from datetime import datetime,timezone
from uuid import uuid4
from enum import Enum
import asyncio,threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Empty,Queue
from feline.config import AppConfig
from feline.core.events import CandleUpdate,Event,FeedHealthEvent,PriceTick,SignalEvent,RiskEvent,OrderUpdate,FillEvent,AIAnalysisResult,RegimeEvent,EconomicEvent
from feline.replay.engine import CSVReplayProvider
from feline.replay.mixed import read_mixed_events,replay_format
from feline.macro.events import NormalizedEconomicEvent,ShockDetector,ShockState,event_phase,measure_horizon
from feline.strategy.macro_event import MacroEventStrategy
from feline.replay.session_report import build_replay_report,export_replay_report,file_checksum
from feline.runtime import FelineRuntime
from feline.market.candles import NativeCandleAggregator
from feline.research.continuous import ContinuousFeatureEngine,ContinuousRegimeEngine,StrategyRouter

class ReplayState(str,Enum):STOPPED="stopped";RUNNING="running";PAUSED="paused"
TIMEFRAME_SECONDS={"1m":60.,"5m":300.,"15m":900.,"1h":3600.}

def shifted_x_range(x_range,timeframe):
 interval=TIMEFRAME_SECONDS[timeframe];return (float(x_range[0])+interval,float(x_range[1])+interval)

def visible_candle_y_range(candles,x_range,padding_fraction=.04):
 left,right=map(float,x_range);visible=[c for c in candles if float(c["open_timestamp"])<=right and float(c["close_timestamp"])>=left]
 if not visible:return None
 low=min(float(c["low"]) for c in visible);high=max(float(c["high"]) for c in visible);span=high-low;scale=max(abs(low),abs(high),1.);padding=max(span*padding_fraction,scale*1e-6,1e-9);return (low-padding,high+padding)

def should_follow_candle(enabled,is_new,previous_timestamp,instrument,timeframe,selected_instrument,selected_timeframe):
 return bool(enabled and is_new and previous_timestamp is not None and instrument==selected_instrument and timeframe==selected_timeframe)

class ChartBuffer:
 def __init__(self,limit=5000):self.points=deque(maxlen=limit);self.candles={x:deque(maxlen=limit) for x in ("1m","5m","15m","1h")};self.markers=deque(maxlen=500);self.needs_fit=True
 def add(self,timestamp,price):self.points.append((timestamp,price))
 def add_candle(self,value):
  series=self.candles[value["timeframe"]]
  is_new=not series or series[-1]["open_timestamp"]!=value["open_timestamp"]
  if not is_new:series[-1]=value
  else:series.append(value)
  if not self.points or self.points[-1][0]!=value["close_timestamp"]:self.points.append((value["close_timestamp"],value["close"]))
  return is_new
 def consume_fit(self):value=self.needs_fit;self.needs_fit=False;return value
 def request_fit(self):self.needs_fit=True
class EventProjection:
 def __init__(self,limit=1000):self.rows=deque(maxlen=limit)
 def add(self,timestamp,category,description,instrument=None,details=None):self.rows.append({"timestamp":timestamp,"category":category,"instrument":instrument,"description":description,"details":details or {}})
class ReplayController:
 def __init__(self):self.state=ReplayState.STOPPED;self.speed="1";self.dataset=None;self._gate=threading.Event();self._gate.set();self._stop=threading.Event()
 def configure(self,dataset,speed):self.dataset=dataset;self.speed=speed
 def start(self):self.state=ReplayState.RUNNING;self._stop.clear();self._gate.set()
 def pause(self):
  if self.state is ReplayState.RUNNING:self.state=ReplayState.PAUSED;self._gate.clear()
 def resume(self):
  if self.state is ReplayState.PAUSED:self.state=ReplayState.RUNNING;self._gate.set()
 def stop(self):self.state=ReplayState.STOPPED;self._stop.set();self._gate.set()

class RuntimeThread:
 """Owns an asyncio loop off the Qt thread; callbacks receive projections only."""
 def __init__(self):self.thread=None;self.loop=True;self.executor=None
 def start(self):
  if self.executor:return
  self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix="feline-core")
 def submit(self,coro):
  if not self.executor:raise RuntimeError("controller not started")
  return self.executor.submit(asyncio.run,coro)
 def stop(self):
  if self.executor:self.executor.shutdown(wait=True,cancel_futures=True);self.executor=None

class ControlledReplayProvider(CSVReplayProvider):
 def __init__(self,path,speed,control,seed=0):super().__init__(Path(path),"max" if speed=="MAX" else speed,seed);self.control=control
 async def stream(self):
  async for tick in super().stream():
   while self.control.state is ReplayState.PAUSED and not self.control._stop.is_set():await asyncio.sleep(.03)
   if self.control._stop.is_set():break
   yield tick

class WorkstationController:
 """Real runtime bridge. Qt polls bounded projections; core never waits for UI."""
 def __init__(self,config:AppConfig|None=None,limit=2000):
  self.config=config or AppConfig();self.replay=ReplayController();self.worker=RuntimeThread();self.research_executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix="feline-research");self.messages=Queue(maxsize=limit);self.runtime=None;self.future=None;self.research_future=None;self.research_cancel=threading.Event();self.selected_instrument=None;self.dropped=0;self.macro=None;self.phase=None;self.shock=ShockState.CALM;self.strategy={"state":"WAITING_FOR_EVENT","reason":""};self.continuous=None;self.continuous_events=[];self.continuous_features=ContinuousFeatureEngine();self.continuous_regimes=ContinuousRegimeEngine();self.continuous_router=StrategyRouter();self.horizons={};self.abstentions={"evaluated":0,"trades":0,"no_trade":0};self._macro_samples=[];self.session=None;self.records={};self.completed_snapshot=None;self.feed={"state":"OFF","provider":None,"last_source_timestamp":None,"last_ingestion_timestamp":None};self.mode="replay"
 def _emit(self,item):
  if self.session:item.setdefault("replay_session_id",self.session["replay_session_id"])
  item.setdefault("ingestion_timestamp",datetime.now(timezone.utc).isoformat())
  try:self.messages.put_nowait(item)
  except Exception:self.dropped+=1
 async def _event(self,event):
  if isinstance(event,FeedHealthEvent):
   self.feed={"state":event.state,"provider":event.provider,"last_source_timestamp":event.last_source_timestamp.isoformat() if event.last_source_timestamp else None,"last_ingestion_timestamp":event.last_ingestion_timestamp.isoformat() if event.last_ingestion_timestamp else None};self._emit({"kind":"feed",**self.feed});return
  if isinstance(event,CandleUpdate):self._emit_candle(event);return
  category="MARKET" if isinstance(event,PriceTick) else "STRATEGY" if isinstance(event,SignalEvent) else "RISK" if isinstance(event,RiskEvent) else "ORDER" if isinstance(event,OrderUpdate) else "FILL" if isinstance(event,FillEvent) else "AI" if isinstance(event,AIAnalysisResult) else "SYSTEM"
  if isinstance(event,PriceTick):
   if self.session:self.session["replay_start_timestamp"]=self.session["replay_start_timestamp"] or event.timestamp.isoformat();self.session["replay_end_timestamp"]=event.timestamp.isoformat();self.session["instruments"]=sorted(set(self.session["instruments"]+[event.instrument]))
   row={"kind":"tick","timestamp":event.timestamp.isoformat(),"source_timestamp":event.timestamp.isoformat(),"instrument":event.instrument,"bid":event.bid,"ask":event.ask,"price":event.mid,"spread":event.spread_ratio};self._record("market",row);self._emit({**row,"timestamp":event.timestamp.timestamp()})
  elif not isinstance(event,RegimeEvent):
   row={"kind":"event","timestamp":event.timestamp.isoformat(),"source_timestamp":event.timestamp.isoformat(),"category":category,"instrument":getattr(event,"instrument",None),"summary":type(event).__name__,"payload":event.payload()};self._record(category.lower(),row);self._emit(row)
 def _record(self,category,item):
  row=dict(item)
  if self.session:row.setdefault("replay_session_id",self.session["replay_session_id"])
  self.records.setdefault(category,[]).append(row)
 def start_replay(self,dataset,speed="MAX",seed=0):
  self.mode="replay";self.feed={"state":"SYNTHETIC","provider":"replay","last_source_timestamp":None,"last_ingestion_timestamp":None}
  self.stop()
  if self.future:
   try:self.future.result(timeout=3)
   except Exception:pass
  if self.runtime:self.runtime.database.close();self.runtime=None
  try:kind=replay_format(Path(dataset))
  except ValueError as exc:self._emit({"kind":"diagnostic","severity":"error","summary":str(exc)});raise
  while not self.messages.empty():
   try:self.messages.get_nowait()
   except Empty:break
  self.macro=None;self.phase=None;self.shock=ShockState.CALM;self.strategy={"state":"WAITING_FOR_EVENT","reason":""};self.continuous=None;self.continuous_events=[];self.continuous_features=ContinuousFeatureEngine();self.continuous_regimes=ContinuousRegimeEngine();self.continuous_router=StrategyRouter();self.horizons={};self.abstentions={"evaluated":0,"trades":0,"no_trade":0};self._macro_samples=[];self.records={"signals":[],"risk":[],"macro":[],"ai":[],"diagnostics":[]};self.completed_snapshot=None
  self.session={"replay_session_id":str(uuid4()),"dataset_path":str(Path(dataset).resolve()),"dataset_name":Path(dataset).name,"dataset_checksum":file_checksum(Path(dataset)),"replay_speed":speed,"seed":seed,"replay_start_timestamp":None,"replay_end_timestamp":None,"instruments":[],"strategy_configuration":{"mode":"macro_only" if kind=="jsonl" else "reference","reference_enabled":kind=="csv"},"risk_configuration":asdict(self.config.risk),"execution_assumptions":asdict(self.config.paper),"starting_equity":self.config.paper.initial_cash,"source_data_type":"tick","candle_timeframe":None,"providers":[],"ohlc_available":False,"volume_available":False,"spread_provenance":"provider bid/ask" if kind=="csv" else "fixture bid/ask","execution_bid_ask_synthetic":False}
  self.replay.configure(dataset,speed);self.replay.start();self.worker.start();provider=ControlledReplayProvider(dataset,speed,self.replay,seed) if kind=="csv" else None;runtime_config=self.config if kind=="csv" else replace(self.config,strategy=replace(self.config.strategy,enabled=False));self.runtime=FelineRuntime(runtime_config,provider=provider,recover=False,replay_session_id=self.session["replay_session_id"]);self.runtime.bus.subscribe(Event,self._event)
  self.runtime.database.save_replay_session(self.session,"running")
  async def run():
   failure=None
   try:
    if kind=="csv":await self.runtime.run()
    else:await self._run_mixed(Path(dataset),speed)
   except Exception as exc:
    failure=exc;row={"kind":"diagnostic","severity":"error","summary":f"{type(exc).__name__}: {exc}","source_timestamp":self.session.get("replay_end_timestamp")};self._record("diagnostics",row);self._emit(row);raise
   finally:
    if kind=="jsonl":self.runtime.snapshot();self.runtime.database.persist_broker_state(self.runtime.broker)
    self.replay.stop();self.completed_snapshot=self.snapshot();status="failed" if failure else "completed";self.runtime.database.save_replay_session({**self.session,"result":build_replay_report(self.session,self.completed_snapshot,self.records)},status);self._emit({"kind":"state","state":status})
  self.future=self.worker.submit(run());self._emit({"kind":"state","state":"running","dataset":dataset})
 def start_realtime(self,instruments=("EURUSD",),environment="practice"):
  self.stop()
  if self.future:
   try:self.future.result(timeout=3)
   except Exception:pass
  if self.runtime:self.runtime.database.close();self.runtime=None
  from feline.market.oanda import OandaV20Provider
  from feline.market.realtime import RealtimeIngestionProvider,RealtimeSessionConfig
  from dataclasses import replace as dc_replace
  source=OandaV20Provider(environment=environment);provider=RealtimeIngestionProvider(source,RealtimeSessionConfig(instruments=tuple(instruments)))
  self.mode="realtime";self.session=None;self.records={"signals":[],"risk":[],"candles":[],"diagnostics":[]};self.feed={"state":"CONNECTING","provider":"oanda_v20","last_source_timestamp":None,"last_ingestion_timestamp":None};self.replay.start();self.worker.start();self.runtime=FelineRuntime(dc_replace(self.config,ai=dc_replace(self.config.ai,enabled=False)),provider=provider,recover=True);self.runtime.bus.subscribe(Event,self._event)
  async def run():
   try:await self.runtime.run()
   finally:self.replay.stop();self.completed_snapshot=self.snapshot();self._emit({"kind":"state","state":"stopped"})
  self.future=self.worker.submit(run());self._emit({"kind":"state","state":"running","mode":"realtime","session_id":provider.session_id});return provider.session_id
 async def _run_mixed(self,path,speed):
  from datetime import timedelta
  events=read_mixed_events(path);self.continuous_events=[event for event in events if isinstance(event,NormalizedEconomicEvent)];previous_time=None;previous_price=None;detector=ShockDetector();strategy=MacroEventStrategy();native_aggregator=NativeCandleAggregator()
  for index,event in enumerate(events):
   while self.replay.state is ReplayState.PAUSED and not self.replay._stop.is_set():await asyncio.sleep(.03)
   if self.replay._stop.is_set():break
   timestamp=event.timestamp if isinstance(event,(PriceTick,CandleUpdate)) else event.scheduled_at
   if previous_time and speed!="MAX":await asyncio.sleep(min(1.,max(0,(timestamp-previous_time).total_seconds()/float(speed))))
   previous_time=timestamp;self.session["replay_start_timestamp"]=self.session["replay_start_timestamp"] or timestamp.isoformat();self.session["replay_end_timestamp"]=timestamp.isoformat()
   if isinstance(event,NormalizedEconomicEvent):
    self.macro=event;self.phase=event_phase(event,timestamp).value;self.strategy={"state":"PRE_EVENT","reason":"scheduled macro event"};self._macro_samples=[] if previous_price is None else [(timestamp,previous_price,0.)];row={"kind":"macro","timestamp":timestamp.timestamp(),"source_timestamp":timestamp.isoformat(),"event":vars(event),"phase":self.phase};self._record("macro",row);self._emit(row);await self.runtime.bus.publish(EconomicEvent(timestamp=timestamp,name=event.title,event_type=event.event_type,importance=event.importance,scheduled_at=event.scheduled_at,replay_session_id=self.session["replay_session_id"]))
   else:
    if isinstance(event,CandleUpdate):
     self.session.update({"source_data_type":"native_ohlc","candle_timeframe":event.timeframe,"ohlc_available":True,"volume_available":self.session["volume_available"] or event.volume>0,"spread_provenance":"configured synthetic execution spread; provider OHLC is price/mid, not bid/ask","execution_bid_ask_synthetic":True});self.session["providers"]=sorted(set(self.session["providers"]+[event.source]));event=replace(event,replay_session_id=self.session["replay_session_id"]);execution_spread=self.config.paper.synthetic_spread_bps/10000;self.runtime.trades.update(event.instrument,event.low*(1-execution_spread/2),event.low*(1+execution_spread/2));self.runtime.trades.update(event.instrument,event.high*(1-execution_spread/2),event.high*(1+execution_spread/2));tick=PriceTick(timestamp=event.close_time,instrument=event.instrument,bid=event.close*(1-execution_spread/2),ask=event.close*(1+execution_spread/2),volume=event.volume,source=f"{event.source}:synthetic_execution",replay_session_id=self.session["replay_session_id"]);await self.runtime.handle_tick(tick);await self.runtime.bus.publish(event);price=event.close;high=event.high;low=event.low;spread_ratio=tick.spread_ratio;self._emit_candle(event)
     for aggregate in native_aggregator.update(event):await self.runtime.bus.publish(aggregate);self._emit_candle(aggregate)
    else:await self.runtime.handle_tick(event);price=event.mid;high=low=price;spread_ratio=event.spread_ratio
    if event.instrument not in self.session["instruments"]:self.session["instruments"].append(event.instrument)
    if self.macro:
     ret=0 if previous_price is None else price/previous_price-1;old=self.shock;self.shock=detector.update(ret,spread_ratio,ret);elapsed=(timestamp-self.macro.scheduled_at).total_seconds()/60
     if old is ShockState.SHOCK and elapsed>=5 and spread_ratio<.003:self.shock=ShockState.STABILIZED
     phase="stabilization" if self.shock is ShockState.STABILIZED and elapsed<=15 else event_phase(self.macro,timestamp,shock=self.shock).value;self._macro_samples.append((timestamp,price,spread_ratio,high,low));row={"kind":"macro_state","timestamp":timestamp.timestamp(),"source_timestamp":timestamp.isoformat(),"phase":phase,"shock":self.shock.value,"return":ret,"velocity":ret,"spread":spread_ratio,"instrument":event.instrument};self._record("macro",row);self._emit(row)
     if phase!=self.phase or self.shock!=old:self._emit({"kind":"marker","instrument":event.instrument,"timestamp":timestamp.timestamp(),"label":phase if phase!=self.phase else self.shock.value,"category":"MACRO"});self.phase=phase
     for horizon in (1,5,15,30,60):
      if horizon not in self.horizons and elapsed>=horizon:
       samples=[x for x in self._macro_samples if x[0]<=self.macro.scheduled_at+timedelta(minutes=horizon)]
       if len(samples)>1:self.horizons[horizon]={**vars(measure_horizon([x[1] for x in samples],[x[2] for x in samples],horizon,[x[3] if len(x)>3 else x[1] for x in samples],[x[4] if len(x)>4 else x[1] for x in samples])),"replay_session_id":self.session["replay_session_id"],"macro_event_id":self.macro.event_id,"instrument":event.instrument}
     if (self.shock is ShockState.STABILIZED or elapsed>=5) and self.strategy["state"] not in {"CONTINUATION","MEAN_REVERSION","NO_TRADE"} and len(self._macro_samples)>=3:
      from feline.research.postshock import decision_diagnostics
      initial=self._macro_samples[1][1]/self._macro_samples[0][1]-1;post=price/self._macro_samples[-2][1]-1;decision=strategy.evaluate(initial,post,self.shock,spread_ratio);diagnostics=decision_diagnostics(initial,post,self.shock.value,spread_ratio);self.abstentions["evaluated"]+=1;state=decision.outcome.value.upper();self.strategy={"state":state,"reason":decision.reason,"direction":decision.direction,"confidence":decision.confidence,"decision_diagnostics":diagnostics};self.abstentions["no_trade"]+=int(state=="NO_TRADE");row={"kind":"signal","timestamp":timestamp.isoformat(),"source_timestamp":timestamp.isoformat(),"instrument":event.instrument,"strategy":"macro_event","strategy_version":"0.8.1","source_event_id":self.macro.event_id,"outcome":state,"direction":decision.direction,"confidence":decision.confidence,"reason":decision.reason,"decision_diagnostics":diagnostics};self._record("signals",row);self._emit(row);self._emit({"kind":"marker","instrument":event.instrument,"timestamp":timestamp.timestamp(),"label":state,"category":"STRATEGY"})
    previous_price=price
   self._emit({"kind":"progress","index":index+1,"total":len(events),"timestamp":timestamp.isoformat()})
  for aggregate in native_aggregator.flush():await self.runtime.bus.publish(aggregate);self._emit_candle(aggregate)
 def _emit_candle(self,event):
  row={"kind":"candle","instrument":event.instrument,"timeframe":event.timeframe,"timestamp":event.close_time.isoformat(),"open_timestamp":event.open_time.timestamp(),"close_timestamp":event.close_time.timestamp(),"open":event.open,"high":event.high,"low":event.low,"close":event.close,"volume":event.volume,"source":event.source,"provenance":event.provenance};existing=self.records.setdefault("candles",[])
  if existing and existing[-1].get("instrument")==event.instrument and existing[-1].get("timeframe")==event.timeframe and existing[-1].get("open_timestamp")==row["open_timestamp"]:return
  self._record("candles",row);self._emit(row)
  if event.timeframe=="1m" and event.complete:
   try:
    snapshot=self.continuous_features.update(event,self.continuous_events);regime=self.continuous_regimes.classify(snapshot);decision=self.continuous_router.route(snapshot,regime);self.continuous={"regime":regime.regime.value,"strength":regime.regime_strength,"strategy":decision.strategy_family.value,"signal":decision.signal,"reason":decision.reason,"event_risk":regime.regime.value=="EVENT_RISK","timestamp":event.close_time.isoformat()};self._emit({"kind":"continuous",**self.continuous})
   except ValueError as exc:self._emit({"kind":"diagnostic","severity":"warning","summary":f"continuous projection: {exc}"})
 def pause(self):self.replay.pause();self._emit({"kind":"state","state":"paused"})
 def resume(self):self.replay.resume();self._emit({"kind":"state","state":"running"})
 def stop(self):
  self.replay.stop()
  if self.runtime:
   self.runtime.running=False
   if self.mode=="realtime":Path("data/REALTIME_STOP").parent.mkdir(parents=True,exist_ok=True);Path("data/REALTIME_STOP").write_text("GUI graceful stop\n")
 def emergency_stop(self):
  if self.runtime:self.runtime.risk.activate_kill_switch()
  Path("data/EMERGENCY_STOP").parent.mkdir(parents=True,exist_ok=True);Path("data/EMERGENCY_STOP").write_text("Qt emergency stop\n");self._emit({"kind":"state","state":"kill_switch"})
 def snapshot(self):
  if not self.runtime:return None
  session_id=self.session["replay_session_id"] if self.session else self.runtime.realtime_session_id;p=self.runtime.broker.portfolio_state();return {"portfolio":p,"risk":{"trading_enabled":self.runtime.risk.trading_enabled,"kill_switch":self.runtime.risk.kill_switch,"danger":self.runtime.risk.danger.active(),"daily_pnl":self.runtime.risk.state.daily_pnl,"max_exposure":self.runtime.config.risk.max_total_exposure},"prices":{k:{"bid":v.bid,"ask":v.ask,"mid":v.mid,"spread":v.spread_ratio,"regime":self.runtime.regimes.current.get(k).value if k in self.runtime.regimes.current else "unknown"} for k,v in self.runtime.broker.quotes.items()},"ai":{"queue":self.runtime.ai.queue.qsize(),"available":self.runtime.ai.last_available,"model":self.runtime.config.ai.model},"positions":[vars(x) for x in self.runtime.broker.positions.values()],"orders":[{**x.payload(),"replay_session_id":session_id} for x in self.runtime.broker.orders.values()],"fills":[{**x.payload(),"replay_session_id":session_id} for x in self.runtime.broker.fills[-100:]],"trades":[{**vars(x),"replay_session_id":session_id} for x in self.runtime.trades.completed],"macro":vars(self.macro) if self.macro else None,"phase":self.phase,"shock":self.shock.value,"strategy":self.strategy,"continuous":self.continuous,"horizons":self.horizons,"abstentions":self.abstentions,"replay_session_id":session_id,"realtime_session_id":self.runtime.realtime_session_id,"feed":self.feed,"mode":self.mode,"dropped":self.dropped}
 def build_report(self):
  if not self.session or not self.completed_snapshot:raise RuntimeError("no completed replay result")
  return build_replay_report(self.session,self.completed_snapshot,self.records)
 def export_report(self,path):return export_replay_report(self.build_report(),Path(path))
 def start_research(self,manifest,output_root="data/reports/research"):
  if self.future and not self.future.done():raise RuntimeError("stop the active replay before batch research")
  if self.research_future and not self.research_future.done():raise RuntimeError("research experiment already running")
  from feline.research.engine import run_experiment
  self.research_cancel.clear()
  def progress(value):self._emit({"kind":"research",**value})
  self.research_future=self.research_executor.submit(run_experiment,Path(manifest),self.config,Path(output_root),False,progress,self.research_cancel);return self.research_future
 def cancel_research(self):self.research_cancel.set()
 def drain(self,maximum=250):
  result=[]
  for _ in range(maximum):
   try:result.append(self.messages.get_nowait())
   except Empty:break
  return result
 def shutdown(self):
  self.stop()
  self.cancel_research()
  if self.research_future:
   try:self.research_future.result(timeout=5)
   except Exception:pass
  if self.future:
   try:self.future.result(timeout=3)
   except Exception:pass
  if self.runtime:
   try:self.worker.submit(self.runtime.stop()).result(timeout=3)
   except Exception:pass
   self.runtime.database.close();self.runtime=None
  self.worker.stop()
  self.research_executor.shutdown(wait=True,cancel_futures=True)
