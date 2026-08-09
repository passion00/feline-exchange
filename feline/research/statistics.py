from __future__ import annotations
from collections import Counter,defaultdict
import math,random,statistics

def describe(values):
 values=list(values)
 if not values:return {"n":0,"mean":None,"median":None,"std":None,"min":None,"max":None,"positive_fraction":None,"negative_fraction":None}
 return {"n":len(values),"mean":statistics.fmean(values),"median":statistics.median(values),"std":statistics.stdev(values) if len(values)>1 else 0.,"min":min(values),"max":max(values),"positive_fraction":sum(x>0 for x in values)/len(values),"negative_fraction":sum(x<0 for x in values)/len(values)}

def bootstrap_interval(values,statistic="mean",samples=500,seed=0,confidence=.95):
 values=list(values)
 if not values:return {"n":0,"low":None,"high":None,"samples":samples}
 rng=random.Random(seed);fn=statistics.fmean if statistic=="mean" else statistics.median;draws=sorted(fn(rng.choices(values,k=len(values))) for _ in range(samples));tail=(1-confidence)/2;return {"n":len(values),"low":draws[int(tail*samples)],"high":draws[min(samples-1,int((1-tail)*samples))],"samples":samples,"seed":seed}

def aggregate_results(results,bootstrap_samples=500,seed=0):
 included=[x for x in results if x.get("status")=="included"];outcomes=Counter(x.get("strategy_outcome","NO_TRADE") for x in included);directions=Counter(x.get("direction","neutral") for x in included);banks=Counter(x.get("central_bank","unknown") for x in included);horizon_stats={}
 for horizon in (1,5,15,30,60,120):
  rows=[x["horizons"][str(horizon)] for x in included if str(horizon) in x.get("horizons",{}) and x["horizons"][str(horizon)].get("use_in_aggregate",True)];returns=[x["return_value"] for x in rows];mae=[x["mae"] for x in rows];mfe=[x["mfe"] for x in rows];relations=Counter(x.get("initial_shock_relation","neutral") for x in rows);n_rel=relations["continuation"]+relations["reversal"]
  horizon_stats[str(horizon)]={**describe(returns),"mean_mae":statistics.fmean(mae) if mae else None,"median_mae":statistics.median(mae) if mae else None,"mean_mfe":statistics.fmean(mfe) if mfe else None,"median_mfe":statistics.median(mfe) if mfe else None,"clean":sum(x.get("contamination_status")=="clean" for x in rows),"contaminated":sum(x.get("contamination_status")!="clean" for x in rows),"initial_shock_baseline":{**dict(relations),"continuation_rate":relations["continuation"]/n_rel if n_rel else None,"reversal_rate":relations["reversal"]/n_rel if n_rel else None},"mean_ci":bootstrap_interval(returns,"mean",bootstrap_samples,seed+horizon),"median_ci":bootstrap_interval(returns,"median",bootstrap_samples,seed+horizon)}
 shocks=[abs(x.get("shock_magnitude",0)) for x in included];missed=sum(x.get("missed_move_candidate",False) for x in included);no_trade_reasons=Counter(x.get("no_trade_reason") for x in included if x.get("strategy_outcome")=="NO_TRADE")
 stabilization=[x["stabilization_duration_seconds"] for x in included if x.get("stabilization_duration_seconds") is not None]
 post_outcomes=Counter(x.get("post_stabilization_outcome","NO_STABILIZATION") for x in included);post_stats={};incremental_stats={}
 for horizon in (5,15,30,60):
  post_rows=[x["post_stabilization_horizons"][str(horizon)] for x in included if str(horizon) in x.get("post_stabilization_horizons",{}) and x["post_stabilization_horizons"][str(horizon)].get("use_in_aggregate",True)];values=[x["return_value"] for x in post_rows];incremental=[x["incremental_horizons"][str(horizon)]["return_value"] for x in included if str(horizon) in x.get("incremental_horizons",{})];post_stats[str(horizon)]={**describe(values),"mean_mae":statistics.fmean([x["mae"] for x in post_rows]) if post_rows else None,"mean_mfe":statistics.fmean([x["mfe"] for x in post_rows]) if post_rows else None,"clean":sum(x["status"]=="clean" for x in post_rows),"contaminated":sum(x["status"]!="clean" for x in post_rows),"mean_ci":bootstrap_interval(values,"mean",bootstrap_samples,seed+1000+horizon)};incremental_stats[str(horizon)]={**describe(incremental),"mean_ci":bootstrap_interval(incremental,"mean",bootstrap_samples,seed+2000+horizon)}
 retracements=[x["retracement_fraction"] for x in included if x.get("retracement_fraction") is not None];retentions=[x["impulse_retention_fraction"] for x in included if x.get("impulse_retention_fraction") is not None]
 return {"counts":{"total":len(results),"included":len(included),"excluded":len(results)-len(included),"FOMC":sum(v for k,v in banks.items() if "FED" in k or "FOMC" in k),"ECB":sum(v for k,v in banks.items() if "ECB" in k)},"strategy_outcomes":dict(outcomes),"post_stabilization_outcomes":dict(post_outcomes),"directions":dict(directions),"central_banks":dict(banks),"horizons":horizon_stats,"incremental_horizons":incremental_stats,"post_stabilization_horizons":post_stats,"average_shock_magnitude":statistics.fmean(shocks) if shocks else None,"median_shock_magnitude":statistics.median(shocks) if shocks else None,"stabilization_seconds":describe(stabilization),"retracement_fraction":describe(retracements),"impulse_retention_fraction":describe(retentions),"with_stabilization":len(stabilization),"without_stabilization":len(included)-len(stabilization),"no_trade_frequency":outcomes.get("NO_TRADE",0)/len(included) if included else None,"no_trade_reasons":dict(no_trade_reasons),"missed_move_candidates":missed}

def group_results(results,fields):
 grouped=defaultdict(list)
 for result in results:
  key=tuple(result.get(field) for field in fields);grouped[key].append(result)
 return {"|".join(map(str,key)):aggregate_results(rows,100,0) for key,rows in grouped.items()}
