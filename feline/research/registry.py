from __future__ import annotations
from dataclasses import asdict,dataclass
from datetime import datetime,timezone
from pathlib import Path
from feline.core.events import CandleUpdate,PriceTick
from feline.replay.mixed import read_mixed_events
from feline.replay.session_report import file_checksum

@dataclass(frozen=True)
class DatasetRecord:
 path:str;checksum:str;provider:str;instrument:str;timeframe:str|None;first_timestamp:str;last_timestamp:str;provenance:str;volume_available:bool;spread_provenance:str;import_timestamp:str;license_notes:str="local file; operator is responsible for provider licensing"

def inspect_dataset(path:Path,expected_instrument:str|None=None)->DatasetRecord:
 if not path.exists():raise FileNotFoundError(path)
 events=read_mixed_events(path);market=[x for x in events if isinstance(x,(CandleUpdate,PriceTick))]
 if not market:raise ValueError(f"dataset has no market events: {path}")
 instruments={x.instrument for x in market}
 if len(instruments)!=1:raise ValueError(f"dataset must contain one instrument, found {sorted(instruments)}")
 instrument=next(iter(instruments))
 if expected_instrument and instrument!=expected_instrument:raise ValueError(f"wrong instrument: expected {expected_instrument}, got {instrument}")
 timestamps=[x.timestamp for x in market]
 if len({(x.instrument,x.timestamp,type(x).__name__) for x in market})!=len(market):raise ValueError("overlapping duplicate market records")
 candles=[x for x in market if isinstance(x,CandleUpdate)];provider=market[0].source;timeframe=candles[0].timeframe if candles else None;provenance=candles[0].provenance if candles else "tick";volume=any(x.volume>0 for x in market);spread="provider bid/ask" if not candles else "synthetic execution spread; provider OHLC is price/mid"
 return DatasetRecord(str(path.resolve()),file_checksum(path),provider,instrument,timeframe,min(timestamps).isoformat(),max(timestamps).isoformat(),provenance,volume,spread,datetime.now(timezone.utc).isoformat())
