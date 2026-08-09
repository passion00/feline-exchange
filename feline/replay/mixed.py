from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from feline.core.events import PriceTick
from feline.macro.events import NormalizedEconomicEvent

def read_mixed_events(path:Path):
 events=[]
 with path.open() as handle:
  for line in handle:
   row=json.loads(line);timestamp=datetime.fromisoformat(row["timestamp"].replace("Z","+00:00"))
   if row["type"]=="price":event=PriceTick(timestamp=timestamp,instrument=row["instrument"],bid=row["bid"],ask=row["ask"],volume=row.get("volume",0),source=row.get("source","fixture"))
   else:event=NormalizedEconomicEvent(row["id"],row["source"],row["region"],row["event_type"],row["title"],timestamp,importance=row.get("importance","high"),instruments=tuple(row.get("instruments",[])))
   events.append((timestamp,event))
 return [event for _,event in sorted(events,key=lambda x:x[0])]
