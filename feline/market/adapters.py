from __future__ import annotations

import asyncio
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib import parse, request

from feline.core.events import EconomicEvent,NewsEvent,PriceTick
from .providers import EconomicCalendarProvider,MarketDataProvider,NewsProvider


class AlphaVantageFXProvider(MarketDataProvider):
    """Read-only polling adapter. An API key is supplied by the operator, never to AI."""
    def __init__(self, api_key: str, from_symbol: str, to_symbol: str, interval: float=60, retries: int=3) -> None:
        self.api_key,self.from_symbol,self.to_symbol,self.interval,self.retries=api_key,from_symbol,to_symbol,interval,retries

    async def stream(self):
        failures=0
        while True:
            try:
                tick=await asyncio.to_thread(self._fetch); failures=0
                if tick: yield tick
                await asyncio.sleep(self.interval)
            except Exception:
                failures+=1
                if failures>self.retries:return
                await asyncio.sleep(min(60,2**failures))

    def _fetch(self):
        query=parse.urlencode({"function":"CURRENCY_EXCHANGE_RATE","from_currency":self.from_symbol,"to_currency":self.to_symbol,"apikey":self.api_key})
        with request.urlopen("https://www.alphavantage.co/query?"+query,timeout=10) as response:data=json.load(response)
        row=data.get("Realtime Currency Exchange Rate",{}); rate=float(row.get("5. Exchange Rate",0))
        return PriceTick(instrument=self.from_symbol+self.to_symbol,bid=rate,ask=rate,source="alpha_vantage",timestamp=datetime.now(timezone.utc)) if rate else None


class RSSNewsProvider(NewsProvider):
    """Bounded read-only RSS poller suitable for official feeds such as ECB RSS."""
    def __init__(self,url:str,source:str,poll_interval:float=300,retries:int=3) -> None:
        self.url,self.source,self.poll_interval,self.retries=url,source,poll_interval,retries;self.seen:set[str]=set()

    async def stream(self):
        failures=0
        while True:
            try:
                items=await asyncio.to_thread(self._fetch);failures=0
                for event in items:
                    if event.id not in self.seen:self.seen.add(event.id);yield event
                await asyncio.sleep(self.poll_interval)
            except Exception:
                failures+=1
                if failures>self.retries:return
                await asyncio.sleep(min(60,2**failures))

    def _fetch(self):
        req=request.Request(self.url,headers={"User-Agent":"FelineExchange/0.2 read-only observer"})
        with request.urlopen(req,timeout=10) as response:root=ET.fromstring(response.read())
        results=[]
        for item in root.findall(".//item"):
            title=item.findtext("title") or "";description=item.findtext("description") or "";guid=item.findtext("guid") or item.findtext("link") or title
            import hashlib
            identifier=hashlib.sha256(guid.encode()).hexdigest()
            results.append(NewsEvent(id=identifier,headline=title,body=description,source=self.source))
        return results


class ECBNewsProvider(RSSNewsProvider):
    def __init__(self,poll_interval:float=300,retries:int=3) -> None:
        super().__init__("https://www.ecb.europa.eu/rss/press.html","ecb",poll_interval,retries)


class StaticEconomicCalendarProvider(EconomicCalendarProvider):
    """Offline/research adapter for operator-supplied official calendar exports."""
    def __init__(self,events:list[EconomicEvent])->None:self.events=sorted(events,key=lambda e:e.scheduled_at or e.timestamp)
    async def stream(self):
        for event in self.events:yield event
