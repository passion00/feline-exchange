from __future__ import annotations

from collections import Counter
import csv,json,statistics
from pathlib import Path

def _directory(path:Path)->Path:return path if path.is_dir() else path.parent
def _describe(values):
 if not values:return {"n":0,"mean":None,"median":None,"positive_fraction":None,"negative_fraction":None}
 return {"n":len(values),"mean":statistics.fmean(values),"median":statistics.median(values),"positive_fraction":sum(x>0 for x in values)/len(values),"negative_fraction":sum(x<0 for x in values)/len(values)}

def direction_normalized(rows,horizon,horizon_rows=()):
 by_event={x["event_id"]:x for x in rows};values=[]
 selected=[x for x in horizon_rows if x.get("reference_basis")=="stabilization" and x.get("horizon_minutes")==str(horizon) and x.get("contamination_status") in {"","clean"}]
 for measurement in selected:
  event=by_event.get(measurement["event_id"],{});shock=float(event.get("shock_magnitude") or 0);value=measurement.get("return_value")
  if value not in (None,"") and shock:values.append(float(value)*(1 if shock>0 else -1))
 if not selected:
  for event in rows:
   shock=float(event.get("shock_magnitude") or 0);value=event.get(f"stabilization_to_{horizon}m_return")
   if value not in (None,"") and shock:values.append(float(value)*(1 if shock>0 else -1))
 return _describe(values)

def clean_post_returns(horizon_rows,horizon):
 return _describe([float(x["return_value"]) for x in horizon_rows if x.get("reference_basis")=="stabilization" and x.get("horizon_minutes")==str(horizon) and x.get("contamination_status") in {"","clean"} and x.get("return_value") not in {None,""}])

def summarize_experiment(path:Path)->dict:
 directory=_directory(path);experiment=json.loads((directory/"experiment.json").read_text());aggregate=experiment.get("aggregate",{});events=[];horizon_rows=[]
 if (directory/"events.csv").exists():
  with (directory/"events.csv").open() as handle:events=list(csv.DictReader(handle))
 if (directory/"horizons.csv").exists():
  with (directory/"horizons.csv").open() as handle:horizon_rows=list(csv.DictReader(handle))
 outcomes=Counter(x.get("strategy_outcome") for x in events if x.get("strategy_outcome"));reasons=Counter(x.get("no_trade_reason") for x in events if x.get("strategy_outcome")=="NO_TRADE" and x.get("no_trade_reason"));post=Counter(x.get("post_stabilization_outcome") for x in events if x.get("post_stabilization_outcome") not in {None,"","null","None"});stabilization=[float(x["stabilization_duration_seconds"]) for x in events if x.get("stabilization_duration_seconds")];shocks=[abs(float(x["shock_magnitude"])) for x in events if x.get("shock_magnitude")];buckets=Counter("small" if x<.002 else "medium" if x<.005 else "large" for x in shocks)
 return {"experiment_id":experiment.get("experiment_id"),"event_count":len(events) or aggregate.get("counts",{}).get("included",0),"strategy_outcomes":dict(outcomes) or aggregate.get("strategy_outcomes",{}),"no_trade_reasons":dict(reasons) or aggregate.get("no_trade_reasons",{}),"post_stabilization_outcomes":dict(post) or aggregate.get("post_stabilization_outcomes",{}),"stabilization_rate":len(stabilization)/len(events) if events else None,"stabilization_seconds":_describe(stabilization),"shock_magnitude":_describe(shocks),"shock_buckets":dict(buckets),"missed_move_candidates":sum(str(x.get("missed_move_candidate")).lower()=="true" for x in events) if events else aggregate.get("missed_move_candidates"),"clean_post_stabilization":{"5":clean_post_returns(horizon_rows,5),"15":clean_post_returns(horizon_rows,15)},"direction_normalized_post_stabilization":{"5":direction_normalized(events,5,horizon_rows),"15":direction_normalized(events,15,horizon_rows)},"source":str(directory)}

def compare_experiments(paths):return {"schema_version":"1.0","experiments":[summarize_experiment(Path(path)) for path in paths]}
