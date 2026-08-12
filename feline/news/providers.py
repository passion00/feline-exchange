from __future__ import annotations
import asyncio,hashlib
from dataclasses import dataclass
from datetime import datetime,timezone
from email.utils import parsedate_to_datetime
from typing import AsyncIterator,Protocol
from urllib import request
from xml.etree import ElementTree

from feline.core.events import NewsEvent

@dataclass(frozen=True)
class NewsProviderHealth:
    provider:str;state:str;message:str="";last_success:datetime|None=None;failures:int=0

class NewsProvider(Protocol):
    provider_name:str
    async def stream(self)->AsyncIterator[NewsEvent]:...
    async def stop(self)->None:...

class FixtureNewsProvider:
    provider_name="fixture"
    def __init__(self,events):self.events=tuple(events);self.running=True;self.health=NewsProviderHealth(self.provider_name,"READY")
    async def stream(self):
        for event in self.events:
            if not self.running:break
            yield event
    async def stop(self):self.running=False

def parse_feed(payload:bytes,source_url:str,ingested_at:datetime|None=None)->list[NewsEvent]:
    ingested_at=ingested_at or datetime.now(timezone.utc);root=ElementTree.fromstring(payload);rows=[]
    for item in list(root.findall(".//item"))+list(root.findall("{*}entry")):
        def text(*names):
            for name in names:
                node=item.find(name)
                if node is None:node=item.find("{*}"+name)
                if node is not None and node.text:return node.text.strip()
            return ""
        headline=text("title");body=text("description","summary","content");link=text("link")
        link_node=item.find("{*}link")
        if link_node is not None and link_node.attrib.get("href"):link=link_node.attrib["href"]
        identity=text("guid","id") or link or headline;published=text("pubDate","published","updated")
        try:stamp=parsedate_to_datetime(published) if "," in published else datetime.fromisoformat(published.replace("Z","+00:00"))
        except Exception:stamp=ingested_at
        if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=timezone.utc)
        if headline:rows.append(NewsEvent(id=hashlib.sha256((source_url+"|"+identity).encode()).hexdigest()[:32],timestamp=stamp.astimezone(timezone.utc),headline=headline,body=body,source=source_url,ingestion_timestamp=ingested_at,source_url=link or source_url,provider_event_id=identity))
    return rows

class RSSNewsProvider:
    provider_name="rss"
    def __init__(self,urls,poll_interval=60.,timeout=10.,max_seen=10_000):self.urls=tuple(urls);self.poll_interval=poll_interval;self.timeout=timeout;self.max_seen=max_seen;self.running=True;self.seen=set();self.health=NewsProviderHealth(self.provider_name,"CONFIGURED" if self.urls else "NOT_CONFIGURED")
    def _download(self,url):
        req=request.Request(url,headers={"User-Agent":"FelineExchange/0.17.6 RSS"})
        with request.urlopen(req,timeout=self.timeout) as response:return response.read()
    async def stream(self):
        failures=0
        while self.running:
            for url in self.urls:
                try:
                    payload=await asyncio.to_thread(self._download,url);now=datetime.now(timezone.utc)
                    for event in parse_feed(payload,url,now):
                        if event.id not in self.seen:
                            if len(self.seen)>=self.max_seen:self.seen.clear()
                            self.seen.add(event.id);yield event
                    failures=0;self.health=NewsProviderHealth(self.provider_name,"HEALTHY",last_success=now)
                except Exception as exc:failures+=1;self.health=NewsProviderHealth(self.provider_name,"DEGRADED",type(exc).__name__,self.health.last_success,failures)
            if not self.urls:break
            await asyncio.sleep(self.poll_interval)
    async def stop(self):self.running=False
