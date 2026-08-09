from __future__ import annotations
from dataclasses import dataclass,field
from datetime import datetime,timezone
import json
from pathlib import Path

@dataclass(frozen=True)
class ResearchEvent:
 event_id:str;central_bank:str;event_type:str;title:str;instruments:tuple[str,...];region:str;scheduled_timestamp:datetime;source:str;actual_timestamp:datetime|None=None;source_url:str|None=None;importance:str="high";previous:float|None=None;consensus:float|None=None;actual:float|None=None;unit:str|None=None;surprise:float|None=None;surprise_normalized:float|None=None;notes:str="";tags:tuple[str,...]=();primary_event:bool=True;excluded:bool=False;exclusion_reason:str|None=None;relationship:str|None=None
 def __post_init__(self):
  if not self.event_id.strip():raise ValueError("empty event_id")
  if self.scheduled_timestamp.tzinfo is None:raise ValueError(f"event {self.event_id} timestamp must include timezone")
  if not self.instruments:raise ValueError(f"event {self.event_id} has no instruments")

@dataclass(frozen=True)
class ManifestEntry:
 event:ResearchEvent;dataset_path:Path;window_before_minutes:int=60;window_after_minutes:int=120;secondary_events:tuple[ResearchEvent,...]=()

@dataclass(frozen=True)
class ResearchManifest:
 entries:tuple[ManifestEntry,...];seed:int=0;contamination_policy:str="flag";split:tuple[float,float,float]=(.6,.2,.2);missed_move_threshold:float=.003;bootstrap_samples:int=500;post_stabilization_flat_tolerance:float=.001;post_stabilization_classification_minutes:int=15

def _time(value):return datetime.fromisoformat(value.replace("Z","+00:00")) if value else None
def event_from_dict(row:dict,primary_default=True)->ResearchEvent:
 expected=row.get("consensus");actual=row.get("actual");surprise=row.get("surprise",actual-expected if actual is not None and expected is not None else None)
 return ResearchEvent(row["event_id"],row.get("central_bank",row.get("source","unknown")).upper(),row.get("event_type","other"),row.get("title",row["event_id"]),tuple(row.get("instruments",[row.get("instrument","EURUSD")])),row.get("region","unknown"),_time(row.get("scheduled_timestamp") or row.get("timestamp")),row.get("source","unknown"),_time(row.get("actual_timestamp")),row.get("source_url"),row.get("importance","high"),row.get("previous"),expected,actual,row.get("unit"),surprise,row.get("surprise_normalized"),row.get("notes",""),tuple(row.get("tags",[])),row.get("primary_event",primary_default),row.get("excluded",False),row.get("exclusion_reason"),row.get("relationship"))

def load_manifest(path:Path)->ResearchManifest:
 data=json.loads(path.read_text(encoding="utf-8"));base=path.parent;seen=set();entries=[]
 for row in data.get("events",[]):
  event=event_from_dict(row)
  if event.event_id in seen:raise ValueError(f"duplicate event_id: {event.event_id}")
  seen.add(event.event_id);dataset=Path(row["dataset_path"]);dataset=dataset if dataset.is_absolute() else (base/dataset).resolve();secondary=tuple(event_from_dict(x,False) for x in row.get("secondary_events",[]));entries.append(ManifestEntry(event,dataset,int(row.get("window_before_minutes",data.get("window_before_minutes",60))),int(row.get("window_after_minutes",data.get("window_after_minutes",120))),secondary))
 split=tuple(data.get("split",[.6,.2,.2]));
 if len(split)!=3 or abs(sum(split)-1)>1e-9:raise ValueError("split must contain TRAIN/VALIDATION/TEST fractions summing to one")
 policy=data.get("contamination_policy","flag")
 if policy not in {"flag","censor"}:raise ValueError("contamination_policy must be flag or censor")
 flat=float(data.get("post_stabilization_flat_tolerance",.001));classification=int(data.get("post_stabilization_classification_minutes",15))
 if flat<0:raise ValueError("post_stabilization_flat_tolerance must be non-negative")
 if classification not in {5,15,30,60}:raise ValueError("post_stabilization_classification_minutes must be 5, 15, 30 or 60")
 return ResearchManifest(tuple(entries),int(data.get("seed",0)),policy,split,float(data.get("missed_move_threshold",.003)),int(data.get("bootstrap_samples",500)),flat,classification)
