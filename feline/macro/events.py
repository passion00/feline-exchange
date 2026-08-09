from __future__ import annotations
from dataclasses import dataclass,field
from datetime import datetime,timedelta
from enum import Enum
import math

class EventPhase(str,Enum):PRE_EVENT="pre_event";ANNOUNCEMENT="announcement";INITIAL_SHOCK="initial_shock";STABILIZATION="stabilization";POST_EVENT="post_event";COMPLETE="complete"
class ShockState(str,Enum):CALM="calm";SHOCK="shock";RECOVERING="recovering";STABILIZED="stabilized"
class MacroOutcome(str,Enum):CONTINUATION="continuation";MEAN_REVERSION="mean_reversion";NO_TRADE="no_trade"

@dataclass(frozen=True)
class NormalizedEconomicEvent:
 event_id:str;source:str;region:str;event_type:str;title:str;scheduled_at:datetime;actual_at:datetime|None=None;previous:float|None=None;consensus:float|None=None;actual:float|None=None;unit:str|None=None;importance:str="normal";instruments:tuple[str,...]=();source_url:str|None=None
 @property
 def surprise(self):return self.actual-self.consensus if self.actual is not None and self.consensus is not None else None

@dataclass(frozen=True)
class PhaseConfig:
 pre_minutes:int=30;announcement_seconds:int=30;shock_minutes:int=5;stabilization_minutes:int=15;post_minutes:int=60

def event_phase(event:NormalizedEconomicEvent,now:datetime,config:PhaseConfig=PhaseConfig(),shock:ShockState=ShockState.CALM)->EventPhase:
 delta=(now-event.scheduled_at).total_seconds()
 if delta< -config.pre_minutes*60:return EventPhase.COMPLETE
 if delta<0:return EventPhase.PRE_EVENT
 if delta<=config.announcement_seconds:return EventPhase.ANNOUNCEMENT
 if shock is ShockState.SHOCK or delta<=config.shock_minutes*60:return EventPhase.INITIAL_SHOCK
 if shock in {ShockState.RECOVERING,ShockState.STABILIZED} and delta<=config.stabilization_minutes*60:return EventPhase.STABILIZATION
 if delta<=config.post_minutes*60:return EventPhase.POST_EVENT
 return EventPhase.COMPLETE

@dataclass(frozen=True)
class ShockConfig:
 return_threshold:float=.01;spread_threshold:float=.005;velocity_threshold:float=.001;stabilization_ratio:float=.35;minimum_stable_samples:int=3

class ShockDetector:
 def __init__(self,config:ShockConfig=ShockConfig()):self.config=config;self.state=ShockState.CALM;self.peak=0.;self.stable=0
 def update(self,ret:float,spread:float,velocity:float)->ShockState:
  intensity=max(abs(ret)/self.config.return_threshold if self.config.return_threshold else 0,spread/self.config.spread_threshold if self.config.spread_threshold else 0,abs(velocity)/self.config.velocity_threshold if self.config.velocity_threshold else 0);self.peak=max(self.peak,intensity)
  if intensity>=1:self.state=ShockState.SHOCK;self.stable=0
  elif self.state is ShockState.SHOCK:self.state=ShockState.RECOVERING;self.stable=1
  elif self.state is ShockState.RECOVERING:
   self.stable=self.stable+1 if intensity<=max(1,self.peak)*self.config.stabilization_ratio else 0
   if self.stable>=self.config.minimum_stable_samples:self.state=ShockState.STABILIZED
  return self.state

@dataclass
class HorizonMeasurement:
 horizon_minutes:int;return_value:float;mae:float;mfe:float;volatility:float;average_spread:float;classification:MacroOutcome

def measure_horizon(prices:list[float],spreads:list[float],minutes:int,highs:list[float]|None=None,lows:list[float]|None=None)->HorizonMeasurement:
 start=prices[0];ret=prices[-1]/start-1;moves=[p/start-1 for p in prices];mean=sum(moves)/len(moves);vol=math.sqrt(sum((x-mean)**2 for x in moves)/max(1,len(moves)-1));high_moves=[p/start-1 for p in (highs or prices)];low_moves=[p/start-1 for p in (lows or prices)];classification=MacroOutcome.CONTINUATION if abs(ret)>.001 and ret*moves[1 if len(moves)>1 else 0]>=0 else MacroOutcome.MEAN_REVERSION if abs(ret)>.001 else MacroOutcome.NO_TRADE
 return HorizonMeasurement(minutes,ret,min(low_moves),max(high_moves),vol,sum(spreads)/len(spreads),classification)
