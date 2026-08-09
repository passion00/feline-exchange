from __future__ import annotations
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
from feline.replay.session_report import file_checksum

INTERVALS={"1min":timedelta(minutes=1),"5min":timedelta(minutes=5),"15min":timedelta(minutes=15),"1h":timedelta(hours=1)}

def convert_twelvedata_file(input_path:Path,output_path:Path,instrument:str,interval:str="1min",timezone_name:str="UTC")->int:
    """Convert a downloaded time_series response. Provider datetime is candle-open time."""
    if interval not in INTERVALS:raise ValueError(f"unsupported interval: {interval}")
    if timezone_name.upper()!="UTC":raise ValueError("v0.8.2 local importer requires explicit UTC provider timestamps")
    payload=json.loads(input_path.read_text(encoding="utf-8"));values=payload.get("values")
    if not isinstance(values,list):raise ValueError("Twelve Data response has no values array")
    rows=[]
    for value in values:
        opened=datetime.fromisoformat(value["datetime"].replace("Z","+00:00"));opened=opened.replace(tzinfo=timezone.utc) if opened.tzinfo is None else opened.astimezone(timezone.utc);closed=opened+INTERVALS[interval]
        row={"type":"candle","timestamp":closed.isoformat().replace("+00:00","Z"),"open_time":opened.isoformat().replace("+00:00","Z"),"close_time":closed.isoformat().replace("+00:00","Z"),"instrument":instrument,"timeframe":{"1min":"1m","5min":"5m","15min":"15m","1h":"1h"}[interval],"open":float(value["open"]),"high":float(value["high"]),"low":float(value["low"]),"close":float(value["close"]),"volume":float(value.get("volume") or 0),"source":"twelvedata_local_file","provenance":"native"};rows.append((closed,row))
    rows.sort(key=lambda item:item[0]);output_path.parent.mkdir(parents=True,exist_ok=True)
    with output_path.open("w",encoding="utf-8") as handle:
        for _,row in rows:handle.write(json.dumps(row,separators=(",",":"))+"\n")
    return len(rows)

def add_economic_event(input_path:Path,output_path:Path,timestamp:str,event_id:str,title:str,source:str="federal_reserve",region:str="US",instrument:str="EURUSD")->int:
    rows=[json.loads(line) for line in input_path.read_text(encoding="utf-8").splitlines() if line.strip()];when=datetime.fromisoformat(timestamp.replace("Z","+00:00"));rows.append({"type":"economic","timestamp":when.isoformat().replace("+00:00","Z"),"id":event_id,"source":source,"region":region,"event_type":"fomc" if region.upper()=="US" else "central_bank","title":title,"importance":"critical","instruments":[instrument]});rows.sort(key=lambda row:datetime.fromisoformat(row["timestamp"].replace("Z","+00:00")));output_path.parent.mkdir(parents=True,exist_ok=True);output_path.write_text("".join(json.dumps(row,separators=(",",":"))+"\n" for row in rows),encoding="utf-8");return len(rows)

def import_directory(input_directory:Path,output_directory:Path,instrument:str,interval:str="1min",timezone_name:str="UTC")->list[dict]:
 output_directory.mkdir(parents=True,exist_ok=True);results=[]
 for source in sorted(input_directory.glob("*.json")):
  target=output_directory/(source.stem+".jsonl");temporary=output_directory/(source.stem+".tmp.jsonl");count=convert_twelvedata_file(source,temporary,instrument,interval,timezone_name);checksum=file_checksum(temporary)
  if target.exists() and file_checksum(target)==checksum:temporary.unlink();status="reused"
  else:temporary.replace(target);status="converted"
  results.append({"input":str(source),"output":str(target),"candles":count,"checksum":checksum,"status":status})
 return results
