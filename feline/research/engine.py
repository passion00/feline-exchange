from __future__ import annotations
from dataclasses import asdict
from datetime import datetime,timedelta,timezone
from hashlib import sha256
import csv,json,subprocess,tempfile
from pathlib import Path
from uuid import uuid4
from feline import __version__
from feline.config import AppConfig
from feline.gui.controller import WorkstationController
from feline.macro.events import measure_horizon
from feline.replay.session_report import file_checksum,git_commit
from feline.storage.database import Database
from .catalog import ResearchManifest,load_manifest
from .episodes import ResearchEpisode,build_episode,chronological_splits,horizon_contamination
from .statistics import aggregate_results,group_results

RESEARCH_SCHEMA="1.0"

def repository_dirty()->bool:
 try:return bool(subprocess.run(["git","status","--porcelain"],capture_output=True,text=True,timeout=2).stdout.strip())
 except Exception:return True

def _checksum(value)->str:return sha256(json.dumps(value,sort_keys=True,default=str,separators=(",",":")).encode()).hexdigest()

def validate_manifest(path:Path)->dict:
 manifest=load_manifest(path);episodes=[build_episode(x) for x in manifest.entries];return {"valid":not any(x.excluded for x in episodes),"events":len(episodes),"included":sum(not x.excluded for x in episodes),"excluded":[{"event_id":x.entry.event.event_id,"reason":x.exclusion_reason,"quality_flags":x.quality_flags} for x in episodes if x.excluded],"datasets":[asdict(x.dataset) for x in episodes if x.dataset.checksum]}

def _episode_file(episode:ResearchEpisode,directory:Path)->Path:
 event=episode.entry.event;start=event.scheduled_timestamp-timedelta(minutes=episode.entry.window_before_minutes);end=event.scheduled_timestamp+timedelta(minutes=episode.entry.window_after_minutes);rows=[]
 for line in episode.entry.dataset_path.read_text(encoding="utf-8").splitlines():
  if not line.strip():continue
  row=json.loads(line);stamp=datetime.fromisoformat(row["timestamp"].replace("Z","+00:00"))
  if start<=stamp<=end and row.get("type") not in {"economic","macro"}:rows.append(row)
 def event_row(value):return {"type":"economic","timestamp":value.scheduled_timestamp.isoformat().replace("+00:00","Z"),"id":value.event_id,"source":value.source,"region":value.region,"event_type":value.event_type,"title":value.title,"importance":value.importance,"previous":value.previous,"consensus":value.consensus,"actual":value.actual,"unit":value.unit,"instruments":list(value.instruments)}
 rows.append(event_row(event));rows.sort(key=lambda x:datetime.fromisoformat(x["timestamp"].replace("Z","+00:00")));path=directory/f"{event.event_id}.jsonl";path.write_text("".join(json.dumps(x,separators=(",",":"),default=str)+"\n" for x in rows),encoding="utf-8");return path

def _event_result(episode,split,report,records,policy,missed_threshold):
 event=episode.entry.event;horizons={str(k):dict(v) for k,v in report.get("horizons",{}).items()};candles=[x for x in records.get("candles",[]) if x.get("timeframe")==report["metadata"].get("candle_timeframe")];market=candles or records.get("market",[]);scheduled=event.scheduled_timestamp;pre=[x for x in market if datetime.fromisoformat(x["timestamp"])<=scheduled];post=[x for x in market if datetime.fromisoformat(x["timestamp"])>scheduled]
 if pre and post:
  baseline=pre[-1].get("close",pre[-1].get("price"))
  if "120" not in horizons:
   samples=[pre[-1]]+[x for x in post if datetime.fromisoformat(x["timestamp"])<=scheduled+timedelta(minutes=120)]
   if samples and datetime.fromisoformat(samples[-1]["timestamp"])>=scheduled+timedelta(minutes=120):horizons["120"]=asdict(measure_horizon([x.get("close",x.get("price")) for x in samples],[x.get("spread",0.) for x in samples],120,[x.get("high",x.get("price")) for x in samples],[x.get("low",x.get("price")) for x in samples]))
  shock=post[0].get("close",post[0].get("price"))/baseline-1
 else:baseline=None;shock=0
 for key,value in horizons.items():
  contamination=horizon_contamination(episode.entry,int(key),policy);value.update({"contamination_status":contamination["status"],"secondary_event_ids":contamination["secondary_event_ids"],"use_in_aggregate":contamination["use_in_aggregate"],"initial_shock_relation":"continuation" if shock*value["return_value"]>0 else "reversal" if shock*value["return_value"]<0 else "neutral"})
 strategy=report["macro_analysis"].get("strategy") or {};later=max((abs(x["return_value"]) for x in horizons.values()),default=0);macro_states=report["macro_analysis"].get("phase_transitions",[]);shock_row=next((x for x in macro_states if x.get("shock")=="shock"),None);stable=next((x for x in macro_states if x.get("shock")=="stabilized"),None)
 def volatility(rows):
  prices=[x.get("close",x.get("price")) for x in rows];returns=[prices[i]/prices[i-1]-1 for i in range(1,len(prices)) if prices[i-1]]
  if not returns:return 0.
  mean=sum(returns)/len(returns);return (sum((x-mean)**2 for x in returns)/len(returns))**.5
 bucket="small" if abs(shock)<.002 else "medium" if abs(shock)<.005 else "large"
 return {"schema_version":RESEARCH_SCHEMA,"status":"included","event_id":event.event_id,"episode_id":episode.episode_id,"replay_session_id":report["metadata"]["replay_session_id"],"central_bank":event.central_bank,"event_type":event.event_type,"instrument":event.instruments[0],"year":event.scheduled_timestamp.year,"split":split,"primary_timestamp":event.scheduled_timestamp.isoformat(),"dataset_checksum":episode.dataset.checksum,"quality_flags":list(episode.quality_flags),"pre_event_price":baseline,"announcement_candle":post[0] if post else None,"pre_event_volatility":volatility(pre),"post_event_volatility":volatility(post),"spread_assumption":episode.dataset.spread_provenance,"shock_direction":"up" if shock>0 else "down" if shock<0 else "neutral","shock_magnitude":shock,"shock_magnitude_bucket":bucket,"shock_detection_time":shock_row.get("source_timestamp") if shock_row else None,"stabilization_time":stable.get("source_timestamp") if stable else None,"stabilization_duration_seconds":(datetime.fromisoformat(stable["source_timestamp"])-scheduled).total_seconds() if stable else None,"strategy_outcome":strategy.get("state","NO_TRADE"),"direction":strategy.get("direction","neutral"),"confidence":strategy.get("confidence",0),"no_trade_reason":strategy.get("reason") if strategy.get("state")=="NO_TRADE" else None,"horizons":horizons,"maximum_move":max((x["mfe"] for x in horizons.values()),default=None),"reversal_magnitude":min((x["mae"] for x in horizons.values()),default=None),"missed_move_candidate":strategy.get("state")=="NO_TRADE" and later>=missed_threshold,"secondary_events":[asdict(x) for x in episode.entry.secondary_events]}

def export_experiment(directory:Path,experiment:dict,results:list,aggregate:dict,exclusions:list)->None:
 directory.mkdir(parents=True,exist_ok=False);(directory/"experiment.json").write_text(json.dumps({**experiment,"aggregate":aggregate},indent=2,default=str)+"\n",encoding="utf-8")
 event_fields=["event_id","central_bank","event_type","instrument","year","split","primary_timestamp","strategy_outcome","direction","confidence","no_trade_reason","shock_magnitude","stabilization_duration_seconds","missed_move_candidate","quality_flags","replay_session_id"]
 with (directory/"events.csv").open("w",newline="",encoding="utf-8") as handle:
  writer=csv.DictWriter(handle,fieldnames=event_fields);writer.writeheader();writer.writerows({k:json.dumps(row[k]) if isinstance(row.get(k),(list,dict)) else row.get(k) for k in event_fields} for row in results if row["status"]=="included")
 with (directory/"horizons.csv").open("w",newline="",encoding="utf-8") as handle:
  fields=["event_id","split","horizon_minutes","return_value","mae","mfe","volatility","classification","contamination_status","initial_shock_relation"];writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader()
  for row in results:
   for horizon,value in row.get("horizons",{}).items():writer.writerow({"event_id":row["event_id"],"split":row.get("split"),"horizon_minutes":horizon,**{x:value.get(x) for x in fields[3:]}})
 with (directory/"exclusions.csv").open("w",newline="",encoding="utf-8") as handle:
  writer=csv.DictWriter(handle,fieldnames=["event_id","reason","quality_flags"]);writer.writeheader();writer.writerows(exclusions)
 banks=", ".join(sorted(aggregate["central_banks"]));years=sorted({x["year"] for x in results if x.get("year")});out=aggregate["strategy_outcomes"];usable=[(k,v["mean"]) for k,v in aggregate["horizons"].items() if v["mean"] is not None];strong=max(usable,key=lambda x:abs(x[1])) if usable else ("none",None);contaminated=sum(v["contaminated"] for v in aggregate["horizons"].values());reasons=", ".join(f"{x['reason']} ({x['event_id']})" for x in exclusions) or "none"
 (directory/"summary.md").write_text(f"# Historical macro research summary\n\n- Experiment: `{experiment['experiment_id']}`\n- Events: {aggregate['counts']['included']} included / {aggregate['counts']['total']} total\n- Excluded: {aggregate['counts']['excluded']} — {reasons}\n- Central banks: {banks or 'none'}\n- Years: {years}\n- CONTINUATION: {out.get('CONTINUATION',0)}\n- MEAN_REVERSION: {out.get('MEAN_REVERSION',0)}\n- NO_TRADE: {out.get('NO_TRADE',0)}\n- NO_TRADE reasons: {aggregate['no_trade_reasons']}\n- Missed-move candidates after abstention: {aggregate['missed_move_candidates']}\n- Largest absolute mean observed return: {strong[0]}m ({strong[1]})\n- Contaminated horizon measurements: {contaminated}\n\nClassification statistics do not establish profitability. Contaminated horizons, synthetic execution assumptions, small samples, and selection effects must be reviewed.\n",encoding="utf-8")

def run_experiment(manifest_path:Path,config:AppConfig,output_root:Path=Path("data/reports/research"),fail_fast=False,progress=None,cancel_event=None)->dict:
 manifest=load_manifest(manifest_path);episodes=[build_episode(x) for x in manifest.entries];splits=chronological_splits(episodes,manifest.split);experiment_id=str(uuid4());created=datetime.now(timezone.utc).isoformat();manifest_checksum=file_checksum(manifest_path);configuration={"seed":manifest.seed,"contamination_policy":manifest.contamination_policy,"split":manifest.split,"missed_move_threshold":manifest.missed_move_threshold,"risk":asdict(config.risk),"execution":asdict(config.paper)};experiment={"schema_version":RESEARCH_SCHEMA,"experiment_id":experiment_id,"feline_version":__version__,"git_commit":git_commit(),"repository_dirty":repository_dirty(),"manifest_path":str(manifest_path.resolve()),"manifest_checksum":manifest_checksum,"configuration_checksum":_checksum(configuration),"configuration":configuration,"created_at":created,"strategy":"macro_event","strategy_version":"0.8.1","seed":manifest.seed};db=Database(Path(config.database_path));db.save_research_experiment(experiment_id,"running",created,manifest_checksum,experiment);results=[];exclusions=[]
 with tempfile.TemporaryDirectory(prefix="feline-research-") as tmp:
  for index,episode in enumerate(episodes):
   event_id=episode.entry.event.event_id;split=splits[episode.episode_id]
   if cancel_event and cancel_event.is_set():break
   if progress:progress({"experiment_id":experiment_id,"current_event":event_id,"completed":index,"total":len(episodes),"failed":len(exclusions)})
   db.register_dataset(episode.dataset) if episode.dataset.checksum else None
   if episode.excluded:
    row={"status":"excluded","event_id":event_id,"split":split,"reason":episode.exclusion_reason,"quality_flags":list(episode.quality_flags)};results.append(row);exclusions.append({"event_id":event_id,"reason":episode.exclusion_reason,"quality_flags":"|".join(episode.quality_flags)});db.save_research_episode(experiment_id,episode,row,split,"excluded");continue
   controller=None
   try:
    path=_episode_file(episode,Path(tmp));controller=WorkstationController(config);controller.start_replay(str(path),"MAX",manifest.seed);controller.future.result();report=controller.build_report();row=_event_result(episode,split,report,controller.records,manifest.contamination_policy,manifest.missed_move_threshold);results.append(row);db.save_research_episode(experiment_id,episode,row,split,"completed",row["replay_session_id"])
   except Exception as exc:
    row={"status":"excluded","event_id":event_id,"split":split,"reason":f"{type(exc).__name__}: {exc}","quality_flags":["runtime_failure"]};results.append(row);exclusions.append({"event_id":event_id,"reason":row["reason"],"quality_flags":"runtime_failure"});db.save_research_episode(experiment_id,episode,row,split,"excluded")
    if fail_fast:raise
   finally:
    if controller:controller.shutdown()
 aggregate=aggregate_results(results,manifest.bootstrap_samples,manifest.seed);included=[x for x in results if x["status"]=="included"];aggregate["groups"]={"central_bank":group_results(included,["central_bank"]),"split":group_results(included,["split"]),"outcome":group_results(included,["strategy_outcome"]),"shock_direction":group_results(included,["shock_direction"]),"shock_bucket":group_results(included,["shock_magnitude_bucket"]),"year":group_results(included,["year"]),"instrument":group_results(included,["instrument"])};db.save_aggregate_result(experiment_id,aggregate);experiment.update({"status":"cancelled" if cancel_event and cancel_event.is_set() else "completed","included_event_ids":[x["event_id"] for x in results if x["status"]=="included"],"excluded_event_ids":[x["event_id"] for x in results if x["status"]!="included"],"dataset_checksums":{x.entry.event.event_id:x.dataset.checksum for x in episodes}});db.save_research_experiment(experiment_id,experiment["status"],created,manifest_checksum,experiment);db.close();directory=output_root/experiment_id;export_experiment(directory,experiment,results,aggregate,exclusions)
 if progress:progress({"experiment_id":experiment_id,"completed":len(results),"total":len(episodes),"failed":len(exclusions),"state":experiment["status"]})
 return {"experiment":experiment,"results":results,"aggregate":aggregate,"output_directory":str(directory)}
