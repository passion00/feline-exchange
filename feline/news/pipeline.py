from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re

from feline.core.events import NewsEvent
from feline.intelligence.service import JobPriority


@dataclass(frozen=True)
class NormalizedNews:
    event: NewsEvent; fingerprint: str; entities: tuple[str,...]; relevance: float; priority: JobPriority


class NewsPipeline:
    def __init__(self, entity_map: dict[str,str] | None=None, max_seen: int=10_000) -> None:
        self.entity_map=entity_map or {"federal reserve":"USD","ecb":"EURUSD","bitcoin":"BTCUSD","bist":"XU100"}; self.seen:set[str]=set(); self.max_seen=max_seen

    def process(self,event:NewsEvent)->NormalizedNews|None:
        headline=" ".join(event.headline.lower().split()); body=" ".join(event.body.split())
        fingerprint=hashlib.sha256((event.source.lower()+"|"+headline).encode()).hexdigest()
        if fingerprint in self.seen:return None
        if len(self.seen)>=self.max_seen:self.seen.clear()
        self.seen.add(fingerprint)
        entities=tuple(sorted({instrument for phrase,instrument in self.entity_map.items() if phrase in (headline+" "+body.lower())}|set(event.instruments)))
        major=bool(re.search(r"rate decision|cpi|employment|emergency|bankruptcy",headline)); relevance=min(1.0,0.2+0.2*len(entities)+(0.5 if major else 0))
        priority=JobPriority.CRITICAL if major else JobPriority.HIGH if relevance>=0.6 else JobPriority.NORMAL
        normalized=NewsEvent(id=event.id,timestamp=event.timestamp,headline=" ".join(event.headline.split()),body=body,source=event.source,instruments=entities,correlation_id=event.correlation_id)
        return NormalizedNews(normalized,fingerprint,entities,relevance,priority)
