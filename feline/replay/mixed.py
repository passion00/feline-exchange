from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from feline.core.events import CandleUpdate,NewsEvent,PriceTick
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
   elif row["type"] in {"candle","ohlc"}:
    opened=datetime.fromisoformat(row["open_time"].replace("Z","+00:00"));closed=datetime.fromisoformat(row["close_time"].replace("Z","+00:00"));event=CandleUpdate(timestamp=closed,instrument=row["instrument"],timeframe=row.get("timeframe","1m"),open_time=opened,close_time=closed,open=float(row["open"]),high=float(row["high"]),low=float(row["low"]),close=float(row["close"]),volume=float(row.get("volume",0) or 0),tick_count=0,source=row.get("source","unknown"),complete=True,provenance=row.get("provenance","native"))
   elif row["type"] in {"economic","macro"}:event=NormalizedEconomicEvent(row["id"],row["source"],row["region"],row["event_type"],row["title"],timestamp,previous=row.get("previous"),consensus=row.get("consensus"),actual=row.get("actual"),unit=row.get("unit"),importance=row.get("importance","high"),instruments=tuple(row.get("instruments",[])),source_url=row.get("source_url"))
   elif row["type"]=="news":event=NewsEvent(id=row.get("id") or __import__('hashlib').sha256(json.dumps(row,sort_keys=True).encode()).hexdigest()[:32],timestamp=timestamp,headline=row["headline"],body=row.get("body",""),source=row.get("source","fixture"),instruments=tuple(row.get("instruments",[])),source_url=row.get("source_url"),provider_event_id=row.get("provider_event_id"),ingestion_timestamp=datetime.fromisoformat(row.get("ingestion_timestamp",row["timestamp"]).replace("Z","+00:00")))
   else:raise ValueError(f"unsupported mixed replay event type: {row['type']}")
   events.append((timestamp,event))
 return [event for _,event in sorted(events,key=lambda x:x[0])]
