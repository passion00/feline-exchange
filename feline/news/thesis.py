from __future__ import annotations
from dataclasses import dataclass,replace
from datetime import datetime,timedelta,timezone
import hashlib,json

from feline.config import ConfirmationConfig,ThesisConfig
from feline.core.events import AffectedAsset,MarketThesis,NewsEvent,Side,SignalEvent,ThesisState,ThesisStateEvent
from feline.market.universe import InstrumentUniverse

def stable_thesis_id(event_id:str,context_hash:str,data:dict)->str:
    body=json.dumps({"event":event_id,"context":context_hash,"analysis":data},sort_keys=True,separators=(",",":"));return hashlib.sha256(body.encode()).hexdigest()[:24]

def horizon_expiry(value:str,base:datetime,default_minutes:float)->datetime:
    text=value.lower();amount=default_minutes
    import re
    found=re.search(r"(\d+(?:\.\d+)?)\s*(minute|hour|day)",text)
    if found:
        number=float(found.group(1));amount=number*(1 if found.group(2)=="minute" else 60 if found.group(2)=="hour" else 1440)
    return base+timedelta(minutes=max(1,amount))

@dataclass
class FocusEntry:
    thesis_id:str;instrument:str;bias:str;confidence:float;relevance:float;priority:float;started_at:datetime;expires_at:datetime;state:ThesisState;confirmation_state:str="WAITING";reference_price:float|None=None;tradable:bool=False;shortable:bool=False

class FocusManager:
    def __init__(self,config:ThesisConfig):self.config=config;self.theses={};self.focus={};self.events=[]
    def accept(self,thesis:MarketThesis,quotes:dict|None=None):
        self.expire(thesis.created_at);self.theses[thesis.thesis_id]=thesis
        if len(self.theses)>self.config.maximum_active_theses:
            keep=sorted(self.theses.values(),key=lambda x:(x.importance,x.confidence,x.created_at,x.thesis_id),reverse=True)[:self.config.maximum_active_theses];self.theses={x.thesis_id:x for x in keep}
            if thesis.thesis_id not in self.theses:return []
        quotes=quotes or {};candidates=[]
        for asset in thesis.affected_assets:
            state=ThesisState.RESEARCH_ONLY if not asset.tradable or (asset.directional_bias=="SHORT" and not asset.shortable) else ThesisState.WATCHING
            price=quotes.get(asset.instrument).mid if asset.instrument in quotes else None;entry=FocusEntry(thesis.thesis_id,asset.instrument,asset.directional_bias,asset.confidence,asset.relevance,asset.monitoring_priority,thesis.created_at,thesis.expires_at,state,"UNAVAILABLE" if state is ThesisState.RESEARCH_ONLY else "WAITING",price,asset.tradable,bool(asset.shortable));candidates.append(entry)
        active=sorted([*self.focus.values(),*candidates],key=lambda x:(x.state is ThesisState.WATCHING,x.priority,x.confidence,x.relevance,x.started_at),reverse=True)
        self.focus={(x.thesis_id,x.instrument):x for x in active[:self.config.maximum_focused_instruments]}
        for entry in candidates:
            if (entry.thesis_id,entry.instrument) not in self.focus:
                entry.state=ThesisState.REJECTED;entry.confirmation_state="FOCUS_LIMIT"
        return candidates
    def expire(self,now:datetime):
        changed=[]
        for entry in self.focus.values():
            if entry.state is ThesisState.WATCHING and now>entry.expires_at:changed.append(self.transition(entry,ThesisState.EXPIRED,"EXPIRED","thesis expiry reached",now=now))
        return changed
    def transition(self,entry:FocusEntry,state:ThesisState,confirmation:str,reason:str,signal_id=None,now=None,previous=None):
        previous=previous or entry.state;entry.state=state;entry.confirmation_state=confirmation;event=ThesisStateEvent(timestamp=now or entry.started_at,thesis_id=entry.thesis_id,instrument=entry.instrument,previous=previous,current=state,confirmation_state=confirmation,reason=reason,source_signal_id=signal_id,correlation_id=entry.thesis_id);self.events.append(event);return event
    def watching(self,instrument,now):return [x for x in self.focus.values() if x.instrument==instrument and x.state is ThesisState.WATCHING and now<=x.expires_at]

class ThesisConfirmationEngine:
    VERSION="news-confirmation-v1"
    def __init__(self,config:ConfirmationConfig,thesis_config:ThesisConfig):self.config=config;self.thesis_config=thesis_config
    def evaluate(self,signal:SignalEvent,entry:FocusEntry,feed_healthy:bool=True):
        if not feed_healthy:return None,"FEED_UNHEALTHY"
        if signal.timestamp>entry.expires_at:return None,"EXPIRED"
        expected=Side.BUY if entry.bias=="LONG" else Side.SELL if entry.bias=="SHORT" else None
        if expected is None:return None,"NEUTRAL"
        if signal.side is not expected:return None,"OPPOSITE_PRICE_ACTION"
        if signal.strength<self.config.minimum_signal_strength:return None,"WEAK_CONFIRMATION"
        if entry.reference_price and abs(signal.price/entry.reference_price-1)>self.thesis_config.maximum_reference_move_fraction:return None,"STALE_MOVE"
        candidate=replace(signal,id=hashlib.sha256(f"{entry.thesis_id}|{signal.id}".encode()).hexdigest()[:32],strategy="news_thesis_confirmation",strategy_version=self.VERSION,reason=f"Price confirmed {entry.bias} thesis {entry.thesis_id}",correlation_id=entry.thesis_id)
        return candidate,"CONFIRMED"
