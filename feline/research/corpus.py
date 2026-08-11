from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json, os, shutil, time
from pathlib import Path
from urllib import parse, request

from feline.replay.session_report import file_checksum
from feline.replay.twelvedata import convert_twelvedata_file
from feline.research.engine import run_experiment, validate_manifest

UTC=timezone.utc
FOMC_DATES={
 2022:[("01-26",19),("03-16",18),("05-04",18),("06-15",18),("07-27",18),("09-21",18),("11-02",18),("12-14",19)],
 2023:[("02-01",19),("03-22",18),("05-03",18),("06-14",18),("07-26",18),("09-20",18),("11-01",18),("12-13",19)],
 2024:[("01-31",19),("03-20",18),("05-01",18),("06-12",18),("07-31",18),("09-18",18),("11-07",19),("12-18",19)],
 2025:[("01-29",19),("03-19",18),("05-07",18),("06-18",18),("07-30",18),("09-17",18),("10-29",18),("12-10",19)],
}

def fomc_events(year:int,instrument="EURUSD")->list[dict]:
 if year not in FOMC_DATES:raise ValueError(f"no reviewed FOMC schedule for {year}")
 result=[]
 for date,hour in FOMC_DATES[year]:
  stamp=datetime.fromisoformat(f"{year}-{date}T{hour:02d}:00:00+00:00");event_id=f"fomc-{year}-{date}"
  result.append({"event_id":event_id,"central_bank":"FOMC","event_type":"rate_decision","title":f"FOMC decision {year}-{date}","timestamp":stamp.isoformat().replace("+00:00","Z"),"instrument":instrument,"source":"federal_reserve","region":"US","importance":"critical","secondary_events":[{"event_id":event_id+"-press","central_bank":"FOMC","event_type":"press_conference","title":f"FOMC press conference {year}-{date}","timestamp":(stamp+timedelta(minutes=30)).isoformat().replace("+00:00","Z"),"instrument":instrument,"source":"federal_reserve","region":"US","importance":"critical","relationship":"follow_up"}]})
 return result

def _time(value):return datetime.fromisoformat(value.replace("Z","+00:00"))
def _decimal(value):
 try:return Decimal(str(value))
 except (InvalidOperation,ValueError,TypeError) as exc:raise ValueError(f"malformed numeric value: {value!r}") from exc

def validate_twelvedata_payload(payload:dict,event_time:datetime)->dict:
 anomalies=[];duplicates=[];seen=set();rows=[]
 if not isinstance(payload,dict) or not isinstance(payload.get("values"),list):return {"valid":False,"classification":"invalid","reason":"missing values array","candle_count":0,"duplicates":[],"missing_timestamps":[],"ohlc_anomalies":[]}
 if payload.get("status")=="error" or not isinstance(payload.get("meta"),dict) or payload["meta"].get("interval")!="1min":return {"valid":False,"classification":"invalid","reason":"invalid Twelve Data envelope or interval","candle_count":0,"duplicates":[],"missing_timestamps":[],"ohlc_anomalies":[]}
 for index,value in enumerate(payload["values"]):
  try:
   stamp=_time(value["datetime"].replace(" ","T")+('Z' if '+' not in value["datetime"] and not value["datetime"].endswith('Z') else ''));o,h,l,c=(_decimal(value[x]) for x in ("open","high","low","close"))
   if stamp in seen:duplicates.append(stamp.isoformat())
   seen.add(stamp)
   if min(o,h,l,c)<=0:anomalies.append({"timestamp":stamp.isoformat(),"type":"non_positive_price","open":str(o),"high":str(h),"low":str(l),"close":str(c)})
   if not(h>=o and h>=c and l<=o and l<=c and h>=l):anomalies.append({"timestamp":stamp.isoformat(),"type":"ohlc_invariant","open":str(o),"high":str(h),"low":str(l),"close":str(c)})
   rows.append(stamp)
  except Exception as exc:anomalies.append({"row":index,"type":"invalid_row","reason":str(exc)})
 ordered=sorted(set(rows));missing=[]
 if ordered:
  cursor=ordered[0]
  while cursor<=ordered[-1]:
   if cursor not in seen:missing.append(cursor)
   cursor+=timedelta(minutes=1)
 critical_start=event_time-timedelta(minutes=5);critical_end=event_time+timedelta(minutes=30);critical=[x for x in missing if critical_start<=x<=critical_end]
 if duplicates or any(x["type"] in {"invalid_row","non_positive_price"} for x in anomalies):classification="invalid"
 elif anomalies:classification="provider_ohlc_anomaly"
 elif critical:classification="critical_gap"
 elif missing:classification="noncritical_gap"
 else:classification="clean"
 include=classification in {"clean","noncritical_gap"}
 return {"valid":classification!="invalid","classification":classification,"include":include,"reason":{"clean":"complete one-minute sequence","noncritical_gap":"persistent gaps outside announcement -5m through +30m","critical_gap":"persistent gap intersects announcement -5m through +30m","provider_ohlc_anomaly":"provider OHLC invariant requires operator review","invalid":"invalid or duplicate provider rows"}[classification],"actual_first_timestamp":ordered[0].isoformat() if ordered else None,"actual_last_timestamp":ordered[-1].isoformat() if ordered else None,"candle_count":len(rows),"duplicates":duplicates,"missing_timestamps":[x.isoformat() for x in missing],"critical_missing_timestamps":[x.isoformat() for x in critical],"ohlc_anomalies":anomalies}

class TwelveDataDownloader:
 def __init__(self,api_key=None,transport=None,retries=3,minimum_interval=8.):self.api_key=api_key or os.environ.get("FELINE_TWELVE_DATA_API_KEY");self.transport=transport or self._http;self.retries=retries;self.minimum_interval=minimum_interval;self._last=0.
 def _http(self,url,timeout):
  with request.urlopen(request.Request(url,headers={"User-Agent":"FelineExchange/0.17.2 research"}),timeout=timeout) as response:return json.loads(response.read())
 def query(self,instrument,start,end):
  if not self.api_key:raise RuntimeError("FELINE_TWELVE_DATA_API_KEY is not set")
  symbol=instrument[:3]+"/"+instrument[3:];query=parse.urlencode({"symbol":symbol,"interval":"1min","start_date":start.strftime("%Y-%m-%d %H:%M:%S"),"end_date":end.strftime("%Y-%m-%d %H:%M:%S"),"timezone":"UTC","outputsize":5000,"apikey":self.api_key});url="https://api.twelvedata.com/time_series?"+query;error=None
  for attempt in range(self.retries):
   delay=self.minimum_interval-(time.monotonic()-self._last)
   if delay>0:time.sleep(delay)
   try:
    payload=self.transport(url,20);self._last=time.monotonic()
    if payload.get("status")=="error":raise RuntimeError(payload.get("message","provider error"))
    return payload
   except Exception as exc:error=f"{type(exc).__name__}: {str(exc).replace(self.api_key,'***')}"
   if attempt+1<self.retries:time.sleep(min(8,2**attempt))
  raise RuntimeError(f"Twelve Data request failed after {self.retries} attempts: {error}")
 def download(self,path,instrument,start,end,force=False):
  if path.exists() and not force:return "reused"
  payload=self.query(instrument,start,end);event_time=start+timedelta(minutes=61);quality=validate_twelvedata_payload(payload,event_time)
  if not quality["valid"]:raise ValueError(f"provider response rejected: {quality['reason']}")
  path.parent.mkdir(parents=True,exist_ok=True);temporary=path.with_suffix(path.suffix+".tmp");temporary.write_text(json.dumps(payload,indent=2)+"\n",encoding="utf-8")
  if force and path.exists():
   backup=path.with_name(path.stem+f".backup-{file_checksum(path)[:12]}"+path.suffix)
   if not backup.exists():shutil.copy2(path,backup)
  temporary.replace(path);return "downloaded"

def _quality_record(raw,event,downloader=None,recheck=True):
 payload=json.loads(raw.read_text(encoding="utf-8"));event_time=_time(event["timestamp"]);quality=validate_twelvedata_payload(payload,event_time);persistent=list(quality.get("missing_timestamps",[]));rechecks=[]
 if persistent and downloader and recheck:
  missing=[_time(x) for x in persistent];regions=[]
  for stamp in missing:
   if not regions or stamp-regions[-1][-1]>timedelta(minutes=1):regions.append([stamp])
   else:regions[-1].append(stamp)
  present=set()
  for region in regions:
   check=downloader.query(event["instrument"],region[0]-timedelta(minutes=1),region[-1]+timedelta(minutes=1));times={_time(x["datetime"].replace(" ","T")+('Z' if '+' not in x["datetime"] and not x["datetime"].endswith('Z') else '')) for x in check.get("values",[])};reproduced=[x.isoformat() for x in region if x not in times];present|=times;rechecks.append({"start":region[0].isoformat(),"end":region[-1].isoformat(),"reproduced_missing":reproduced})
  persistent=[x for x in persistent if _time(x) not in present];patched={**payload,"values":[x for x in payload["values"]]};quality=validate_twelvedata_payload(patched,event_time);critical_start=event_time-timedelta(minutes=5);critical_end=event_time+timedelta(minutes=30);critical=[x for x in persistent if critical_start<=_time(x)<=critical_end];quality["missing_timestamps"]=persistent;quality["critical_missing_timestamps"]=critical
  if critical:quality.update(classification="critical_gap",include=False,reason="persistent gap intersects announcement -5m through +30m")
  elif persistent:quality.update(classification="noncritical_gap",include=True,reason="persistent gaps outside announcement -5m through +30m")
  elif not quality["ohlc_anomalies"] and not quality["duplicates"]:quality.update(classification="clean",include=True,reason="transient download gap resolved by one recheck")
 quality.update(raw_path=str(raw),raw_checksum=file_checksum(raw),requested_start=(event_time-timedelta(minutes=61)).isoformat(),requested_end=(event_time+timedelta(minutes=121)).isoformat(),gap_recheck=rechecks,provider="twelvedata",instrument=event["instrument"],timeframe="1min",validation_timestamp=datetime.now(UTC).isoformat());return quality

def _manifest(year,events,records,manifest_path,processed_dir):
 included=[]
 for event in events:
  record=records[event["event_id"]]
  if not record["include"]:continue
  row={**event,"dataset_path":str((processed_dir/f"{event['instrument'].lower()}_{event['timestamp'][:10]}.jsonl").relative_to(manifest_path.parent.parent.parent)) if False else f"../../historical/processed/fomc_{year}/{event['instrument'].lower()}_{event['timestamp'][:10]}.jsonl"}
  if record["classification"]=="noncritical_gap":row["tags"]=["persistent_noncritical_gap"]
  included.append(row)
 return {"seed":17,"window_before_minutes":60,"window_after_minutes":120,"split":[.5,.25,.25],"contamination_policy":"flag","bootstrap_samples":1000,"events":included}

def build_corpus(years,instrument="EURUSD",provider="twelvedata",run=False,dry_run=False,force_download=False,skip_download=False,root=Path("data"),downloader=None,config=None,experiment_runner=run_experiment):
 if provider!="twelvedata":raise ValueError("only twelvedata local/provider corpus is supported")
 downloader=downloader or TwelveDataDownloader();summaries=[]
 for year in years:
  events=fomc_events(int(year),instrument);raw_dir=root/"historical"/"raw"/f"fomc_{year}";processed_dir=root/"historical"/"processed"/f"fomc_{year}";manifest_path=root/"research"/"manifests"/f"fomc_{year}.json";quality_path=processed_dir/"data_quality.json";previous_quality=json.loads(quality_path.read_text()).get("events",{}) if quality_path.exists() else {};records={};reused=downloaded=0
  if dry_run:summaries.append({"year":int(year),"scheduled_events":len(events),"would_download":[e["event_id"] for e in events if not(raw_dir/f"{instrument.lower()}_{e['timestamp'][:10]}.json").exists()]});continue
  raw_dir.mkdir(parents=True,exist_ok=True);processed_dir.mkdir(parents=True,exist_ok=True);manifest_path.parent.mkdir(parents=True,exist_ok=True)
  for event in events:
   date=event["timestamp"][:10];raw=raw_dir/f"{instrument.lower()}_{date}.json";processed=processed_dir/f"{instrument.lower()}_{date}.jsonl";start=_time(event["timestamp"])-timedelta(minutes=61);end=_time(event["timestamp"])+timedelta(minutes=121)
   if not raw.exists() and skip_download:records[event["event_id"]]={"classification":"invalid","include":False,"reason":"raw dataset missing with --skip-download","raw_path":str(raw),"raw_checksum":None};continue
   status=downloader.download(raw,instrument,start,end,force_download) if not skip_download else "reused";downloaded+=status=="downloaded";reused+=status=="reused";record=_quality_record(raw,event,downloader if not skip_download else None);previous=previous_quality.get(event["event_id"],{})
   if previous.get("raw_checksum")==record["raw_checksum"]:record["validation_timestamp"]=previous.get("validation_timestamp",record["validation_timestamp"])
   if processed.exists() and previous.get("raw_checksum") not in {None,record["raw_checksum"]}:record.update(classification="invalid",include=False,reason="processed dataset source checksum no longer matches raw dataset; operator review required")
   records[event["event_id"]]=record
   if record["include"] and not processed.exists():convert_twelvedata_file(raw,processed,instrument,"1min","UTC")
   record["processed_path"]=str(processed);record["processed_checksum"]=file_checksum(processed) if processed.exists() else None
  quality={"schema_version":"1.0","central_bank":"FOMC","year":int(year),"events":records};quality_path.write_text(json.dumps(quality,indent=2)+"\n",encoding="utf-8")
  generated=_manifest(int(year),events,records,manifest_path,processed_dir)
  if not manifest_path.exists():manifest_path.write_text(json.dumps(generated,indent=2)+"\n",encoding="utf-8")
  validation=validate_manifest(manifest_path)
  if not validation["valid"]:raise RuntimeError(f"generated manifest validation failed: {validation['excluded']}")
  summary={"year":int(year),"scheduled_events":len(events),"included":sum(x["include"] for x in records.values()),"quarantined":sum(not x["include"] for x in records.values()),"reused_downloads":reused,"new_downloads":downloaded,"manifest":str(manifest_path),"quality_report":str(quality_path)}
  if run:
   if config is None:raise ValueError("config is required with run=True")
   result=experiment_runner(manifest_path,config);summary.update(experiment=result["experiment"]["experiment_id"],report=result["output_directory"])
  summaries.append(summary)
 return summaries

def corpus_doctor(years,instrument="EURUSD",root=Path("data")):
 results=[];ok=True
 for year in years:
  events=fomc_events(int(year),instrument);raw_dir=root/"historical"/"raw"/f"fomc_{year}";processed_dir=root/"historical"/"processed"/f"fomc_{year}";manifest_path=root/"research"/"manifests"/f"fomc_{year}.json";quality_path=processed_dir/"data_quality.json";quality=json.loads(quality_path.read_text()) if quality_path.exists() else {};qevents=quality.get("events",quality);manifest=json.loads(manifest_path.read_text()) if manifest_path.exists() else {"events":[]};included={x["event_id"] for x in manifest.get("events",[])};issues=[];rows=[]
  for event in events:
   date=event["timestamp"][:10];raw=raw_dir/f"{instrument.lower()}_{date}.json";processed=processed_dir/f"{instrument.lower()}_{date}.jsonl";record=qevents.get(event["event_id"],qevents.get(date));classification=(record or {}).get("classification",(record or {}).get("status","missing"));classification={"usable_with_gaps":"noncritical_gap","critical_gaps":"critical_gap"}.get(classification,classification);should_include=(record or {}).get("include",(record or {}).get("research_decision")=="include")
   if not raw.exists():issues.append(f"{event['event_id']}: raw missing")
   if should_include and not processed.exists():issues.append(f"{event['event_id']}: processed missing")
   if record and record.get("raw_checksum") and raw.exists() and record["raw_checksum"]!=file_checksum(raw):issues.append(f"{event['event_id']}: raw checksum mismatch")
   if record is not None and bool(event["event_id"] in included)!=bool(should_include):issues.append(f"{event['event_id']}: quality/manifest inclusion mismatch")
   rows.append({"event_id":event["event_id"],"raw":raw.exists(),"processed":processed.exists(),"quality":classification,"manifest_included":event["event_id"] in included})
  if not quality_path.exists():issues.append("quality report missing")
  if not manifest_path.exists():issues.append("manifest missing")
  elif not issues:
   try:
    validation=validate_manifest(manifest_path)
    if not validation["valid"]:issues.append(f"manifest validation exclusions: {validation['excluded']}")
   except Exception as exc:issues.append(f"manifest invalid: {exc}")
  ok=ok and not issues;results.append({"year":int(year),"events":rows,"issues":issues})
 return {"ok":ok,"results":results}
