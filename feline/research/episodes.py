from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime,timedelta
from uuid import uuid5,NAMESPACE_URL
from feline.core.events import CandleUpdate,PriceTick
from feline.replay.mixed import read_mixed_events
from .catalog import ManifestEntry
from .registry import DatasetRecord,inspect_dataset

@dataclass(frozen=True)
class ResearchEpisode:
 episode_id:str;entry:ManifestEntry;dataset:DatasetRecord;quality_flags:tuple[str,...];excluded:bool=False;exclusion_reason:str|None=None

def build_episode(entry:ManifestEntry)->ResearchEpisode:
 try:dataset=inspect_dataset(entry.dataset_path,entry.event.instruments[0])
 except Exception as exc:return ResearchEpisode(str(uuid5(NAMESPACE_URL,entry.event.event_id)),entry,DatasetRecord(str(entry.dataset_path),"","unknown",entry.event.instruments[0],None,"","","unknown",False,"unknown",""),("invalid_dataset",),True,str(exc))
 start=datetime.fromisoformat(dataset.first_timestamp);end=datetime.fromisoformat(dataset.last_timestamp);event=entry.event.scheduled_timestamp;flags=["native_ohlc" if dataset.provenance=="native" else "reconstructed_ohlc" if dataset.provenance=="reconstructed" else "tick","synthetic_spread" if "synthetic" in dataset.spread_provenance else "provider_spread"]
 reasons=[]
 if start>event-timedelta(minutes=entry.window_before_minutes):flags.append("missing_pre_event");reasons.append("insufficient pre-event coverage")
 if end<event+timedelta(minutes=entry.window_after_minutes):flags.append("missing_post_event");reasons.append("insufficient post-event coverage")
 market=[x for x in read_mixed_events(entry.dataset_path) if isinstance(x,(CandleUpdate,PriceTick))];ordered=sorted(x.timestamp for x in market)
 if dataset.timeframe:
  expected={"1m":60,"5m":300,"15m":900,"1h":3600}.get(dataset.timeframe)
  if expected and any((b-a).total_seconds()>expected*1.5 for a,b in zip(ordered,ordered[1:])):flags.append("timestamp_gap")
 if entry.secondary_events:flags.append("secondary_event_overlap")
 return ResearchEpisode(str(uuid5(NAMESPACE_URL,f"{entry.event.event_id}:{dataset.checksum}:{entry.window_before_minutes}:{entry.window_after_minutes}")),entry,dataset,tuple(flags),bool(reasons) or entry.event.excluded,"; ".join(reasons) or entry.event.exclusion_reason)

def chronological_splits(episodes:list[ResearchEpisode],fractions=(.6,.2,.2))->dict[str,str]:
 ordered=sorted(episodes,key=lambda x:x.entry.event.scheduled_timestamp);n=len(ordered);train=int(n*fractions[0]);validation=int(n*fractions[1]);result={}
 for index,episode in enumerate(ordered):result[episode.episode_id]="TRAIN" if index<train else "VALIDATION" if index<train+validation else "TEST"
 return result

def horizon_contamination(entry:ManifestEntry,horizon_minutes:int,policy:str="flag")->dict:
 end=entry.event.scheduled_timestamp+timedelta(minutes=horizon_minutes);crossed=[x.event_id for x in entry.secondary_events if entry.event.scheduled_timestamp<x.scheduled_timestamp<=end and x.importance in {"high","critical"}]
 return {"status":"censored" if crossed and policy=="censor" else "contains_secondary_event" if crossed else "clean","secondary_event_ids":crossed,"use_in_aggregate":not(crossed and policy=="censor")}
