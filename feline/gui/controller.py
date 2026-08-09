from __future__ import annotations
from collections import deque
from dataclasses import dataclass
from enum import Enum
import asyncio,threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Empty,Queue
from feline.config import AppConfig
from feline.core.events import Event,PriceTick,SignalEvent,RiskEvent,OrderUpdate,FillEvent,AIAnalysisResult,RegimeEvent
from feline.replay.engine import CSVReplayProvider
from feline.runtime import FelineRuntime

class ReplayState(str,Enum):STOPPED="stopped";RUNNING="running";PAUSED="paused"
class ChartBuffer:
 def __init__(self,limit=5000):self.points=deque(maxlen=limit);self.markers=deque(maxlen=500)
 def add(self,timestamp,price):self.points.append((timestamp,price))
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
 def __init__(self,config:AppConfig|None=None,limit=2000):self.config=config or AppConfig();self.replay=ReplayController();self.worker=RuntimeThread();self.messages=Queue(maxsize=limit);self.runtime=None;self.future=None;self.selected_instrument=None;self.dropped=0
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
  self.replay.configure(dataset,speed);self.replay.start();self.worker.start();provider=ControlledReplayProvider(dataset,speed,self.replay);self.runtime=FelineRuntime(self.config,provider=provider,recover=False);self.runtime.bus.subscribe(Event,self._event)
  async def run():
   try:await self.runtime.run()
   finally:self.replay.stop();self._emit({"kind":"state","state":"stopped"})
  self.future=self.worker.submit(run());self._emit({"kind":"state","state":"running","dataset":dataset})
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
  p=self.runtime.broker.portfolio_state();return {"portfolio":p,"risk":{"trading_enabled":self.runtime.risk.trading_enabled,"kill_switch":self.runtime.risk.kill_switch,"danger":self.runtime.risk.danger.active(),"daily_pnl":self.runtime.risk.state.daily_pnl,"max_exposure":self.runtime.config.risk.max_total_exposure},"prices":{k:{"bid":v.bid,"ask":v.ask,"mid":v.mid,"spread":v.spread_ratio,"regime":self.runtime.regimes.current.get(k).value if k in self.runtime.regimes.current else "unknown"} for k,v in self.runtime.broker.quotes.items()},"ai":{"queue":self.runtime.ai.queue.qsize(),"available":self.runtime.ai.last_available,"model":self.runtime.config.ai.model},"positions":[vars(x) for x in self.runtime.broker.positions.values()],"orders":[x.payload() for x in self.runtime.broker.orders.values()],"fills":[x.payload() for x in self.runtime.broker.fills[-100:]],"trades":[vars(x) for x in self.runtime.trades.completed],"dropped":self.dropped}
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
