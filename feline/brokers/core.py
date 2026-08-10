from __future__ import annotations

import json
from abc import ABC,abstractmethod
from dataclasses import asdict,dataclass,field
from datetime import datetime,timezone
from enum import Enum
from pathlib import Path
from typing import AsyncIterator
from uuid import uuid4

from feline.core.events import OrderRequest,OrderUpdate,PriceTick
from feline.execution.broker import Broker


class UnsupportedBrokerCapability(RuntimeError):pass
class BrokerConnectionState(str,Enum):DISCONNECTED="DISCONNECTED";CONNECTING="CONNECTING";CONNECTED="CONNECTED";DEGRADED="DEGRADED";ERROR="ERROR"


@dataclass(frozen=True)
class BrokerCapabilities:
    authentication:bool=True;practice:bool=True;live:bool=False;quotes:bool=True;historical:bool=False;instrument_discovery:bool=False;account:bool=False;positions:bool=False;orders:bool=False;market_orders:bool=False;limit_orders:bool=False;stop_orders:bool=False;modify_orders:bool=False;cancel_orders:bool=False;execution_updates:bool=False
    def supports(self,name:str)->bool:
        if name not in self.__dataclass_fields__:raise ValueError(f"unknown broker capability: {name}")
        return bool(getattr(self,name))


@dataclass(frozen=True)
class BrokerProfile:
    profile_id:str=field(default_factory=lambda:str(uuid4()));name:str="New Broker";adapter:str="oanda_v20";environment:str="practice";account_id:str="";credential_env:str="FELINE_OANDA_API_TOKEN";default_instrument:str="EURUSD";live_execution_enabled:bool=False;created_at:str=field(default_factory=lambda:datetime.now(timezone.utc).isoformat())
    def __post_init__(self):
        if self.environment not in {"paper","practice","demo","live"}:raise ValueError("invalid broker environment")
        if self.environment=="live" and not self.live_execution_enabled:raise ValueError("live profile requires explicit live_execution_enabled")
    def public_dict(self)->dict:return asdict(self)


class BrokerProfileStore:
    """Local non-secret profiles. Credential values remain environment/process only."""
    def __init__(self,path:Path=Path("data/broker_profiles.json")):self.path=path
    def load(self)->list[BrokerProfile]:
        if not self.path.exists():return []
        rows=json.loads(self.path.read_text());return [BrokerProfile(**row) for row in rows]
    def save(self,profile:BrokerProfile)->None:
        profiles={x.profile_id:x for x in self.load()};profiles[profile.profile_id]=profile;self._write(profiles.values())
    def remove(self,profile_id:str)->None:self._write(x for x in self.load() if x.profile_id!=profile_id)
    def get(self,profile_id:str)->BrokerProfile:
        try:return next(x for x in self.load() if x.profile_id==profile_id)
        except StopIteration as exc:raise ValueError("unknown broker profile") from exc
    def _write(self,profiles)->None:
        self.path.parent.mkdir(parents=True,exist_ok=True);payload=[x.public_dict() for x in sorted(profiles,key=lambda p:p.profile_id)];temporary=self.path.with_suffix(".tmp");temporary.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n");temporary.replace(self.path)


class BrokerAdapter(Broker,ABC):
    adapter_name:str;broker_capabilities:BrokerCapabilities;state:BrokerConnectionState=BrokerConnectionState.DISCONNECTED
    @abstractmethod
    async def connect(self)->dict:...
    @abstractmethod
    async def disconnect(self)->None:...
    @abstractmethod
    async def stream(self,instruments:tuple[str,...])->AsyncIterator[PriceTick]:...
    @abstractmethod
    async def account_snapshot(self)->dict:...
    @abstractmethod
    async def discover_instruments(self)->tuple[str,...]:...
    @abstractmethod
    async def reconcile(self)->dict:...
    async def historical_candles(self,request):
        self.require("historical");raise UnsupportedBrokerCapability(f"{self.adapter_name} does not implement historical_candles")
    async def execution_stream(self):
        self.require("execution_updates");raise UnsupportedBrokerCapability(f"{self.adapter_name} does not implement execution_stream")
    def require(self,name:str)->None:
        if not self.broker_capabilities.supports(name):raise UnsupportedBrokerCapability(f"{self.adapter_name} does not support {name}")


class BrokerRegistry:
    def __init__(self):self._factories={}
    def register(self,name:str,factory)->None:self._factories[name.lower()]=factory
    def names(self)->tuple[str,...]:return tuple(sorted(self._factories))
    def create(self,profile:BrokerProfile,**kwargs)->BrokerAdapter:
        try:factory=self._factories[profile.adapter.lower()]
        except KeyError as exc:raise ValueError(f"unknown broker adapter: {profile.adapter}") from exc
        return factory(profile=profile,**kwargs)
    @classmethod
    def builtins(cls):
        from feline.brokers.oanda import OandaBrokerAdapter
        result=cls();result.register("oanda_v20",OandaBrokerAdapter);return result
