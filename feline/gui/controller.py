from __future__ import annotations
from collections import deque
from dataclasses import dataclass
from enum import Enum
import asyncio,threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Empty,Queue
from feline.config import AppConfig
from feline.core.events import Event,PriceTick,SignalEvent,RiskEvent,OrderUpdate,FillEvent,AIAnalysisResult,RegimeEvent,EconomicEvent
from feline.replay.engine import CSVReplayProvider
from feline.replay.mixed import read_mixed_events,replay_format
from feline.macro.events import NormalizedEconomicEvent,ShockDetector,ShockState,event_phase,measure_horizon
from feline.strategy.macro_event import MacroEventStrategy
from feline.runtime import FelineRuntime

class ReplayState(str,Enum):STOPPED="stopped";RUNNING="running";PAUSED="paused"
class ChartBuffer:
 def __init__(self,limit=5000):self.points=deque(maxlen=limit);self.markers=deque(maxlen=500);self.needs_fit=True
 def add(self,timestamp,price):self.points.append((timestamp,price))
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
 def __init__(self,path,speed,control):super().__init__(Path(path),"max" if speed=="MAX" else speed);self.control=control
 async def stream(self):
  async for tick in super().stream():
   while self.control.state is ReplayState.PAUSED and not self.control._stop.is_set():await asyncio.sleep(.03)
   if self.control._stop.is_set():break
   yield tick

class WorkstationController:
 """Real runtime bridge. Qt polls bounded projections; core never waits for UI."""
 def __init__(self,config:AppConfig|None=None,limit=2000):
  self.config=config or AppConfig();self.replay=ReplayController();self.worker=RuntimeThread();self.messages=Queue(maxsize=limit);self.runtime=None;self.future=None;self.selected_instrument=None;self.dropped=0;self.macro=None;self.phase=None;self.shock=ShockState.CALM;self.strategy={"state":"WAITING_FOR_EVENT","reason":""};self.horizons={};self.abstentions={"evaluated":0,"trades":0,"no_trade":0};self._macro_samples=[]
 def _emit(self,item):
  try:self.messages.put_nowait(item)
  except Exception:self.dropped+=1
 async def _event(self,event):
  category="MARKET" if isinstance(event,PriceTick) else "STRATEGY" if isinstance(event,SignalEvent) else "RISK" if isinstance(event,RiskEvent) else "ORDER" if isinstance(event,OrderUpdate) else "FILL" if isinstance(event,FillEvent) else "AI" if isinstance(event,AIAnalysisResult) else "SYSTEM"
  if isinstance(event,PriceTick):self._emit({"kind":"tick","timestamp":event.timestamp.timestamp(),"instrument":event.instrument,"bid":event.bid,"ask":event.ask,"price":event.mid,"spread":event.spread_ratio})
  elif not isinstance(event,RegimeEvent):self._emit({"kind":"event","timestamp":event.timestamp.isoformat(),"category":category,"instrument":getattr(event,"instrument",None),"summary":type(event).__name__,"payload":event.payload()})
 def start_replay(self,dataset,speed="MAX"):
  self.stop()
  if self.future and self.future.done() and self.runtime:
   self.runtime.database.close();self.runtime=None
  try:kind=replay_format(Path(dataset))
  except ValueError as exc:self._emit({"kind":"diagnostic","severity":"error","summary":str(exc)});raise
  self.macro=None;self.phase=None;self.shock=ShockState.CALM;self.strategy={"state":"WAITING_FOR_EVENT","reason":""};self.horizons={};self._macro_samples=[]
  self.replay.configure(dataset,speed);self.replay.start();self.worker.start();provider=ControlledReplayProvider(dataset,speed,self.replay) if kind=="csv" else None;self.runtime=FelineRuntime(self.config,provider=provider,recover=False);self.runtime.bus.subscribe(Event,self._event)
  async def run():
   try:
    if kind=="csv":await self.runtime.run()
    else:await self._run_mixed(Path(dataset),speed)
   finally:self.replay.stop();self._emit({"kind":"state","state":"stopped"})
  self.future=self.worker.submit(run());self._emit({"kind":"state","state":"running","dataset":dataset})
 async def _run_mixed(self,path,speed):
  from datetime import timedelta
  events=read_mixed_events(path);previous_time=None;previous_price=None;detector=ShockDetector();strategy=MacroEventStrategy()
  for index,event in enumerate(events):
   while self.replay.state is ReplayState.PAUSED and not self.replay._stop.is_set():await asyncio.sleep(.03)
   if self.replay._stop.is_set():break
   timestamp=event.timestamp if isinstance(event,PriceTick) else event.scheduled_at
   if previous_time and speed!="MAX":await asyncio.sleep(min(1.,max(0,(timestamp-previous_time).total_seconds()/float(speed))))
   previous_time=timestamp
   if isinstance(event,NormalizedEconomicEvent):
    self.macro=event;self.phase=event_phase(event,timestamp).value;self.strategy={"state":"PRE_EVENT","reason":"scheduled macro event"};self._macro_samples=[] if previous_price is None else [(timestamp,previous_price,0.)];self._emit({"kind":"macro","timestamp":timestamp.timestamp(),"event":vars(event),"phase":self.phase});await self.runtime.bus.publish(EconomicEvent(timestamp=timestamp,name=event.title,event_type=event.event_type,importance=event.importance,scheduled_at=event.scheduled_at))
   else:
    await self.runtime.handle_tick(event);price=event.mid
    if self.macro:
     ret=0 if previous_price is None else price/previous_price-1;old=self.shock;self.shock=detector.update(ret,event.spread_ratio,ret);elapsed=(timestamp-self.macro.scheduled_at).total_seconds()/60
     if old is ShockState.SHOCK and elapsed>=5 and event.spread_ratio<.003:self.shock=ShockState.STABILIZED
     phase="stabilization" if self.shock is ShockState.STABILIZED and elapsed<=15 else event_phase(self.macro,timestamp,shock=self.shock).value;self._macro_samples.append((timestamp,price,event.spread_ratio));self._emit({"kind":"macro_state","timestamp":timestamp.timestamp(),"phase":phase,"shock":self.shock.value,"return":ret,"velocity":ret,"spread":event.spread_ratio})
     if phase!=self.phase or self.shock!=old:self._emit({"kind":"marker","instrument":event.instrument,"timestamp":timestamp.timestamp(),"label":phase if phase!=self.phase else self.shock.value,"category":"MACRO"});self.phase=phase
     for horizon in (1,5,15,30,60):
      if horizon not in self.horizons and elapsed>=horizon:
       samples=[x for x in self._macro_samples if x[0]<=self.macro.scheduled_at+timedelta(minutes=horizon)]
       if len(samples)>1:self.horizons[horizon]=vars(measure_horizon([x[1] for x in samples],[x[2] for x in samples],horizon))
     if (self.shock is ShockState.STABILIZED or elapsed>=5) and self.strategy["state"] not in {"CONTINUATION","MEAN_REVERSION","NO_TRADE"} and len(self._macro_samples)>=3:
      initial=self._macro_samples[1][1]/self._macro_samples[0][1]-1;post=price/self._macro_samples[-2][1]-1;decision=strategy.evaluate(initial,post,self.shock,event.spread_ratio);self.abstentions["evaluated"]+=1;state=decision.outcome.value.upper();self.strategy={"state":state,"reason":decision.reason,"direction":decision.direction,"confidence":decision.confidence};self.abstentions["no_trade"]+=int(state=="NO_TRADE");self._emit({"kind":"signal","timestamp":timestamp.isoformat(),"instrument":event.instrument,"strategy":"macro_event/0.8","outcome":state,"direction":decision.direction,"confidence":decision.confidence,"reason":decision.reason});self._emit({"kind":"marker","instrument":event.instrument,"timestamp":timestamp.timestamp(),"label":state,"category":"STRATEGY"})
    previous_price=price
   self._emit({"kind":"progress","index":index+1,"total":len(events),"timestamp":timestamp.isoformat()})
 def pause(self):self.replay.pause();self._emit({"kind":"state","state":"paused"})
 def resume(self):self.replay.resume();self._emit({"kind":"state","state":"running"})
 def stop(self):
  self.replay.stop()
  if self.runtime:self.runtime.running=False
 def emergency_stop(self):
  if self.runtime:self.runtime.risk.activate_kill_switch()
  Path("data/EMERGENCY_STOP").parent.mkdir(parents=True,exist_ok=True);Path("data/EMERGENCY_STOP").write_text("Qt emergency stop\n");self._emit({"kind":"state","state":"kill_switch"})
 def snapshot(self):
  if not self.runtime:return None
  p=self.runtime.broker.portfolio_state();return {"portfolio":p,"risk":{"trading_enabled":self.runtime.risk.trading_enabled,"kill_switch":self.runtime.risk.kill_switch,"danger":self.runtime.risk.danger.active(),"daily_pnl":self.runtime.risk.state.daily_pnl,"max_exposure":self.runtime.config.risk.max_total_exposure},"prices":{k:{"bid":v.bid,"ask":v.ask,"mid":v.mid,"spread":v.spread_ratio,"regime":self.runtime.regimes.current.get(k).value if k in self.runtime.regimes.current else "unknown"} for k,v in self.runtime.broker.quotes.items()},"ai":{"queue":self.runtime.ai.queue.qsize(),"available":self.runtime.ai.last_available,"model":self.runtime.config.ai.model},"positions":[vars(x) for x in self.runtime.broker.positions.values()],"orders":[x.payload() for x in self.runtime.broker.orders.values()],"fills":[x.payload() for x in self.runtime.broker.fills[-100:]],"trades":[vars(x) for x in self.runtime.trades.completed],"macro":vars(self.macro) if self.macro else None,"phase":self.phase,"shock":self.shock.value,"strategy":self.strategy,"horizons":self.horizons,"abstentions":self.abstentions,"dropped":self.dropped}
 def drain(self,maximum=250):
  result=[]
  for _ in range(maximum):
   try:result.append(self.messages.get_nowait())
   except Empty:break
  return result
 def shutdown(self):
  self.stop()
  if self.future:
   try:self.future.result(timeout=3)
   except Exception:pass
  if self.runtime:
   try:self.worker.submit(self.runtime.stop()).result(timeout=3)
   except Exception:pass
   self.runtime.database.close();self.runtime=None
  self.worker.stop()
