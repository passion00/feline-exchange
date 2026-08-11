"""Safe realtime ingestion boundary between provider transport and runtime."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass,replace
from datetime import datetime,timedelta,timezone
from typing import Awaitable,Callable
from uuid import uuid4

from feline.core.events import FeedHealthEvent,PriceTick
from feline.market.datafeed import FeedState,RealtimeDataProvider,RealtimeIntegrityGuard
from feline.market.providers import MarketDataProvider


@dataclass(frozen=True)
class RealtimeSessionConfig:
    instruments: tuple[str,...]=( "EURUSD", )
    stale_after_seconds: float=10.0
    feed_timeout_seconds: float=15.0
    reconnect_delay_seconds: float=1.0
    maximum_reconnect_delay_seconds: float=30.0


class RealtimeIngestionProvider(MarketDataProvider):
    """Normalizes, timestamps and health-gates an interchangeable live source."""
    def __init__(self,source:RealtimeDataProvider,config:RealtimeSessionConfig=RealtimeSessionConfig(),session_id:str|None=None,
                 health_callback:Callable[[FeedHealthEvent],Awaitable[None]]|None=None):
        self.source=source;self.config=config;self.session_id=session_id or str(uuid4());self.health_callback=health_callback
        self.guard=RealtimeIntegrityGuard(timedelta(seconds=config.stale_after_seconds));self.running=True;self.state=FeedState.CONNECTING;self.sequence=0
        self.last_source_timestamp=None;self.last_ingestion_timestamp=None;self._instrument_generation=0

    def request_focus(self,instruments,max_instruments:int=8)->tuple[str,...]:
        merged=tuple(dict.fromkeys((*self.config.instruments,*(str(x).replace("/","").upper() for x in instruments))))[:max_instruments]
        if merged!=self.config.instruments:self.config=replace(self.config,instruments=merged);self._instrument_generation+=1
        return self.config.instruments

    async def _health(self,state:FeedState,message:str=""):
        self.state=state
        if self.health_callback:
            provider=getattr(getattr(self.source,"capabilities",None),"provider",getattr(self.source,"adapter_name",type(self.source).__name__))
            await self.health_callback(FeedHealthEvent(provider=provider,state=state.value,realtime_session_id=self.session_id,
                last_source_timestamp=self.last_source_timestamp,last_ingestion_timestamp=self.last_ingestion_timestamp,message=message))

    async def stream(self):
        failures=0
        while self.running:
            generation=self._instrument_generation;iterator=self.source.stream(self.config.instruments).__aiter__();await self._health(FeedState.CONNECTING if failures==0 else FeedState.DEGRADED,"connecting")
            try:
                while self.running:
                    if generation!=self._instrument_generation:
                        close=getattr(iterator,"aclose",None)
                        if close:await close()
                        break
                    tick=await asyncio.wait_for(iterator.__anext__(),timeout=self.config.feed_timeout_seconds)
                    tick=self.guard.validate(tick);ingested=datetime.now(timezone.utc);self.sequence+=1
                    self.last_source_timestamp=tick.timestamp;self.last_ingestion_timestamp=ingested;failures=0
                    normalized=replace(tick,ingestion_timestamp=ingested,provider_sequence=self.sequence,realtime_session_id=self.session_id)
                    if self.state is not FeedState.HEALTHY:await self._health(FeedState.HEALTHY,"valid quote received")
                    yield normalized
            except asyncio.CancelledError:raise
            except (StopAsyncIteration,TimeoutError,ConnectionError,OSError,ValueError,KeyError,TypeError) as exc:
                failures+=1;await self._health(FeedState.STALE if isinstance(exc,TimeoutError) else FeedState.DEGRADED,type(exc).__name__)
                close=getattr(iterator,"aclose",None)
                if close:await close()
                if self.running:await asyncio.sleep(min(self.config.maximum_reconnect_delay_seconds,self.config.reconnect_delay_seconds*2**min(failures-1,5)))
        await self._health(FeedState.FAILED,"stopped")

    async def stop(self):
        self.running=False;await self._health(FeedState.FAILED,"operator shutdown")
