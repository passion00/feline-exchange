from __future__ import annotations
from collections import deque
from dataclasses import dataclass
from enum import Enum
import asyncio,threading
from concurrent.futures import ThreadPoolExecutor

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
