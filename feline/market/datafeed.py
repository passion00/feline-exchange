"""Interchangeable historical and realtime market-data contracts."""
from __future__ import annotations

import asyncio
import json
import random
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterator

from feline.core.events import CandleUpdate, PriceTick


class FeedState(str, Enum):
    CONNECTING="CONNECTING"; HEALTHY="HEALTHY"; DEGRADED="DEGRADED"; STALE="STALE"; FAILED="FAILED"


@dataclass(frozen=True)
class ProviderCapabilities:
    provider: str
    historical_candles: bool
    realtime_prices: bool
    bid_ask: bool
    authentication_required: bool
    supported_instruments: tuple[str, ...]


@dataclass(frozen=True)
class HistoricalRequest:
    instrument: str
    start: datetime
    end_exclusive: datetime
    timeframe: str="1m"
    price_basis: str="mid"

    def __post_init__(self):
        if self.start.tzinfo is None or self.end_exclusive.tzinfo is None:raise ValueError("historical boundaries must be timezone-aware")
        if self.start >= self.end_exclusive:raise ValueError("historical start must precede exclusive end")
        if self.timeframe != "1m":raise ValueError("production data layer currently normalizes 1m candles")


class HistoricalDataProvider(ABC):
    capabilities: ProviderCapabilities
    @abstractmethod
    def acquire(self, request: HistoricalRequest, output: Path): ...


class RealtimeDataProvider(ABC):
    capabilities: ProviderCapabilities
    @abstractmethod
    def stream(self, instruments: tuple[str,...]) -> AsyncIterator[PriceTick]: ...


@dataclass(frozen=True)
class HTTPPolicy:
    timeout_seconds: float=30.0
    retries: int=4
    base_backoff_seconds: float=1.0
    minimum_interval_seconds: float=0.1
    jitter_seed: int=0


class ProviderRequestError(RuntimeError): pass


class RetryingHTTPClient:
    """Bounded, rate-limited HTTP; secrets are never included in exceptions."""
    def __init__(self, policy: HTTPPolicy=HTTPPolicy(), opener: Callable=urllib.request.urlopen):
        self.policy=policy;self.opener=opener;self._last=0.;self._rng=random.Random(policy.jitter_seed)

    def open(self, request: urllib.request.Request):
        error=None
        for attempt in range(self.policy.retries):
            delay=self.policy.minimum_interval_seconds-(time.monotonic()-self._last)
            if delay>0:time.sleep(delay)
            try:
                self._last=time.monotonic();return self.opener(request,timeout=self.policy.timeout_seconds)
            except urllib.error.HTTPError as exc:
                error=f"HTTP {exc.code}"
                if exc.code not in {408,429,500,502,503,504}:break
            except (urllib.error.URLError,OSError,TimeoutError) as exc:error=type(exc).__name__
            if attempt+1<self.policy.retries:time.sleep(self.policy.base_backoff_seconds*(2**attempt)+self._rng.random()*.1)
        raise ProviderRequestError(f"provider request failed after {self.policy.retries} attempts ({error})")

    def json(self, request: urllib.request.Request)->dict[str,Any]:
        with self.open(request) as response:return json.load(response)


class RealtimeIntegrityGuard:
    """Reject malformed, reordered, crossed, or stale realtime quotes."""
    def __init__(self, stale_after: timedelta=timedelta(seconds=10), now: Callable[[],datetime]|None=None):
        self.stale_after=stale_after;self.now=now or (lambda:datetime.now(timezone.utc));self.latest:dict[str,datetime]={}

    def validate(self, tick:PriceTick)->PriceTick:
        timestamp=tick.timestamp.astimezone(timezone.utc)
        if not all(map(lambda x:x>0 and x<1e12,(tick.bid,tick.ask))) or tick.bid>tick.ask:raise ValueError("malformed or crossed realtime quote")
        if timestamp>self.now()+timedelta(seconds=2):raise ValueError("future-dated realtime quote")
        if self.now()-timestamp>self.stale_after:raise ValueError("stale realtime quote")
        if timestamp<=self.latest.get(tick.instrument,datetime.min.replace(tzinfo=timezone.utc)):raise ValueError("duplicate or non-monotonic realtime quote")
        self.latest[tick.instrument]=timestamp;return tick


class DataFeedRegistry:
    def __init__(self):self._historical={};self._realtime={}
    def register(self,name:str,provider:object)->None:
        key=name.lower()
        if isinstance(provider,HistoricalDataProvider):self._historical[key]=provider
        if isinstance(provider,RealtimeDataProvider):self._realtime[key]=provider
    def historical(self,name:str)->HistoricalDataProvider:
        try:return self._historical[name.lower()]
        except KeyError as exc:raise ValueError(f"unknown historical provider: {name}") from exc
    def realtime(self,name:str)->RealtimeDataProvider:
        try:return self._realtime[name.lower()]
        except KeyError as exc:raise ValueError(f"unknown realtime provider: {name}") from exc
