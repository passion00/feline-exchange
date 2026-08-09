from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from feline.core.events import PriceTick
from feline.macro.events import NormalizedEconomicEvent

SUPPORTED_REPLAY_SUFFIXES={".csv",".jsonl"}
def replay_format(path:Path)->str:
 suffix=path.suffix.lower()
 if suffix not in SUPPORTED_REPLAY_SUFFIXES:raise ValueError(f"unsupported replay format: {suffix or '<none>'}")
 return suffix[1:]

def read_mixed_events(path:Path):
 events=[]
 with path.open() as handle:
  for line in handle:
   row=json.loads(line);timestamp=datetime.fromisoformat(row["timestamp"].replace("Z","+00:00"))
   if row["type"]=="price":event=PriceTick(timestamp=timestamp,instrument=row["instrument"],bid=row["bid"],ask=row["ask"],volume=row.get("volume",0),source=row.get("source","fixture"))
   elif row["type"] in {"economic","macro"}:event=NormalizedEconomicEvent(row["id"],row["source"],row["region"],row["event_type"],row["title"],timestamp,previous=row.get("previous"),consensus=row.get("consensus"),actual=row.get("actual"),unit=row.get("unit"),importance=row.get("importance","high"),instruments=tuple(row.get("instruments",[])),source_url=row.get("source_url"))
   else:raise ValueError(f"unsupported mixed replay event type: {row['type']}")
   events.append((timestamp,event))
 return [event for _,event in sorted(events,key=lambda x:x[0])]
