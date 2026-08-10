"""Signal-locked research over frozen continuous strategy opportunities.

This is predictor/outcome research, not an execution or portfolio simulator.
"""
from __future__ import annotations

import csv
import json
import math
import random
import subprocess
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable

from feline import __version__
from feline.core.events import CandleUpdate
from feline.market.profiles import ExecutionProfile, get_execution_profile, get_market_profile
from feline.macro.events import NormalizedEconomicEvent
from feline.replay.mixed import read_mixed_events
from feline.replay.session_report import file_checksum
from feline.research.continuous import (CONTINUOUS_ENGINE_VERSION, REGIME_VERSION,
    ContinuousConfig, ContinuousFeatureEngine, ContinuousRegimeEngine,
    StrategyFamily, StrategyRouter)

SIGNAL_STUDY_VERSION = "1.0"
CANONICAL_MODEL_VERSION = "1.0"
DEFAULT_MULTIPLIERS = (0.0, .25, .5, .75, 1.0, 1.5, 2.0)
PREDICTOR_KEYS = (
    "return_1m","return_5m","return_15m","return_30m","return_60m",
    "realized_vol_5m","realized_vol_15m","realized_vol_30m","realized_vol_60m",
    "atr_like_14m","trend_slope_15m","trend_slope_30m","trend_slope_60m",
    "price_vs_ma_15m","price_vs_ma_30m","price_vs_ma_60m","price_zscore_30m",
    "volatility_ratio_short_long","range_ratio_short_long","distance_from_15m_high",
    "distance_from_15m_low","distance_from_30m_high","distance_from_30m_low",
    "position_in_30m_range","breakout_above_prior_30m_high","breakout_below_prior_30m_low",
    "candle_body_fraction","upper_wick_fraction","lower_wick_fraction","candle_range_fraction",
    "utc_hour","weekday","session","minutes_to_next_critical_event",
    "minutes_since_previous_critical_event","critical_event_window_active",
)


@dataclass(frozen=True)
class SignalOpportunity:
    opportunity_id: str
    instrument: str
    dataset_checksum: str
    timestamp: str
    signal_available_at: str
    regime: str
    strategy: str
    direction: str
    reference_price: float
    reference_entry_timestamp: str
    reference_entry_price: float
    stop_reference_price: float
    stop_distance: float
    target_reference_price: float | None
    maximum_holding_bars: int
    predictors: dict[str, Any]
    eligibility: dict[str, Any]
    strategy_version: str
    regime_version: str
    market_profile_version: str
    configuration_checksum: str
    seed: int


def _git_state() -> tuple[str, bool]:
    try:
        commit=subprocess.run(["git","rev-parse","HEAD"],capture_output=True,text=True,check=True,timeout=2).stdout.strip()
        dirty=bool(subprocess.run(["git","status","--porcelain"],capture_output=True,text=True,timeout=2).stdout.strip())
        return commit,dirty
    except Exception:return "unknown",True


def _opportunity_id(checksum: str, instrument: str, timestamp: str, strategy: str,
                    direction: str, config_checksum: str) -> str:
    value={"dataset":checksum,"instrument":instrument,"timestamp":timestamp,"strategy":strategy,
           "direction":direction,"config":config_checksum,"regime_version":REGIME_VERSION,
           "model":CANONICAL_MODEL_VERSION}
    return sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()[:24]


def build_opportunities(dataset: Path, instrument: str, strategy: str = "all", seed: int = 17,
                        config: ContinuousConfig | None = None) -> tuple[list[SignalOpportunity], list[CandleUpdate], dict[str, Any]]:
    from feline.research.market_data import assert_dataset_research_eligible
    assert_dataset_research_eligible(dataset)
    config=config or ContinuousConfig();instrument=instrument.replace("/","").upper();profile=get_market_profile(instrument)
    events=read_mixed_events(dataset);candles=[row for row in events if isinstance(row,CandleUpdate) and row.instrument==instrument and row.timeframe=="1m"]
    macro=[row for row in events if isinstance(row,NormalizedEconomicEvent)];checksum=file_checksum(dataset)
    configuration={"continuous":asdict(config),"strategy":strategy,"canonical_model":CANONICAL_MODEL_VERSION}
    config_checksum=sha256(json.dumps(configuration,sort_keys=True).encode()).hexdigest()
    feature_engine=ContinuousFeatureEngine(config);regime_engine=ContinuousRegimeEngine(config);router=StrategyRouter(config);opportunities=[];candidates=[]
    for candle in candles:
        snapshot=feature_engine.update(candle,macro);regime=regime_engine.classify(snapshot)
        decision=router.route(snapshot,regime,(strategy,),position_open=False)
        reason_code=_reason_code(decision.reason,regime.regime.value)
        candidates.append({"timestamp":candle.close_time.isoformat(),"regime":regime.regime.value,"strategy":decision.strategy_family.value,"eligible":decision.eligible,"reason_code":reason_code})
        if not decision.eligible:continue
        direction="long" if decision.signal=="BUY" else "short";entry=candle.close;stop=entry*(1-.001 if direction=="long" else 1+.001);timestamp=candle.close_time.isoformat()
        predictors={key:snapshot.features.get(key) for key in PREDICTOR_KEYS}
        opportunity_id=_opportunity_id(checksum,instrument,timestamp,decision.strategy_family.value,direction,config_checksum)
        opportunities.append(SignalOpportunity(opportunity_id,instrument,checksum,timestamp,timestamp,regime.regime.value,
            decision.strategy_family.value,direction,entry,timestamp,entry,stop,abs(entry-stop),None,15,predictors,
            {"eligible":True,"reason":decision.reason,"reason_code":reason_code,"strength":decision.strength},
            decision.strategy_version,REGIME_VERSION,profile.profile_version,config_checksum,seed))
    return opportunities,candles,{"configuration":configuration,"configuration_checksum":config_checksum,"candidates":candidates}


def _reason_code(reason: str, regime: str) -> str:
    mapping={"waiting for pullback":"PULLBACK_NOT_CONFIRMED","price near range center":"ZSCORE_TOO_SMALL",
             "no completed compression-breakout sequence":"VOL_EXPANSION_NOT_CONFIRMED",
             "ordinary strategies suppressed by event risk":"EVENT_RISK","insufficient_history":"INSUFFICIENT_HISTORY",
             "no strategy eligible for regime":"WRONG_REGIME"}
    return mapping.get(reason,"ELIGIBLE" if reason in {"modest counter-trend pullback","range-edge displacement","completed breakout after compression"} else regime)


def resolve_canonical_trade(opportunity: SignalOpportunity, candles: list[CandleUpdate], index_by_time:dict[str,int]|None=None) -> dict[str, Any] | None:
    by_time=index_by_time or {row.close_time.isoformat():index for index,row in enumerate(candles)};index=by_time.get(opportunity.reference_entry_timestamp)
    if index is None:return None
    entry=opportunity.reference_entry_price;stop=opportunity.stop_reference_price;direction=opportunity.direction
    exit_price=None;exit_reason="insufficient_future_data";exit_candle=None
    for row in candles[index+1:index+1+opportunity.maximum_holding_bars]:
        if direction=="long" and row.open<=stop:exit_price=row.open;exit_reason="gap_stop";exit_candle=row;break
        if direction=="short" and row.open>=stop:exit_price=row.open;exit_reason="gap_stop";exit_candle=row;break
        if direction=="long" and row.low<=stop:exit_price=stop;exit_reason="stop";exit_candle=row;break
        if direction=="short" and row.high>=stop:exit_price=stop;exit_reason="stop";exit_candle=row;break
        exit_price=row.close;exit_reason="time_exit";exit_candle=row
    if exit_candle is None or len(candles[index+1:index+1+opportunity.maximum_holding_bars])<opportunity.maximum_holding_bars:return None
    pnl=(exit_price-entry) if direction=="long" else (entry-exit_price)
    unit_risk=opportunity.stop_distance*get_market_profile(opportunity.instrument).contract_multiplier
    return {"opportunity_id":opportunity.opportunity_id,"instrument":opportunity.instrument,"strategy":opportunity.strategy,
            "regime":opportunity.regime,"direction":direction,"entry_timestamp":opportunity.reference_entry_timestamp,
            "entry_price":entry,"exit_timestamp":exit_candle.close_time.isoformat(),"exit_price":exit_price,"exit_reason":exit_reason,
            "stop_reference_price":stop,"target_reference_price":opportunity.target_reference_price,
            "holding_bars":min(opportunity.maximum_holding_bars,(exit_candle.close_time-datetime.fromisoformat(opportunity.reference_entry_timestamp)).total_seconds()/60),
            "initial_unit_risk":unit_risk,"reference_gross_price":pnl,"reference_gross_R":pnl*get_market_profile(opportunity.instrument).contract_multiplier/unit_risk,
            "session":opportunity.predictors.get("session"),"utc_hour":opportunity.predictors.get("utc_hour"),"predictors":opportunity.predictors}


def apply_friction_overlay(trade: dict[str, Any], profile: ExecutionProfile, multiplier: float,
                           starting_equity: float=100_000.,risk_fraction: float=.0025) -> dict[str, Any]:
    entry=float(trade["entry_price"]);exit_price=float(trade["exit_price"]);unit_risk=float(trade["initial_unit_risk"])
    spread=((entry+exit_price)/2)*(profile.spread_value/10_000)
    slip_bps=profile.base_slippage_value+profile.spread_value*profile.spread_dependent_slippage
    slippage=(entry+exit_price)*(slip_bps/10_000)
    commission=0.0;financing=0.0
    spread*=multiplier;slippage*=multiplier;commission*=multiplier;financing*=multiplier
    total=spread+slippage+commission+financing;gross=float(trade["reference_gross_price"]);net=gross-total
    result={key:value for key,value in trade.items() if key!="predictors"};result.update({"cost_multiplier":multiplier,
        "execution_profile":profile.profile_name,"calibrated":profile.calibrated,"reference_gross_price":gross,
        "spread_cost_price":spread,"slippage_cost_price":slippage,"commission_cost_price":commission,
        "financing_cost_price":financing,"total_cost_price":total,"hypothetical_net_price":net,
        "reference_gross_R":gross/unit_risk,"spread_cost_R":spread/unit_risk,"slippage_cost_R":slippage/unit_risk,
        "commission_cost_R":commission/unit_risk,"financing_cost_R":financing/unit_risk,"total_cost_R":total/unit_risk,
        "net_R":net/unit_risk,"display_R_usd":starting_equity*risk_fraction,"hypothetical_usd_pnl":net/unit_risk*starting_equity*risk_fraction})
    if abs(result["reference_gross_R"]-result["total_cost_R"]-result["net_R"])>1e-10:raise ValueError("signal overlay does not reconcile")
    return result


def non_overlapping_ids(trades: list[dict[str, Any]]) -> set[str]:
    accepted=set();blocked_until:dict[tuple[str,str],datetime]={}
    for row in sorted(trades,key=lambda item:(item["entry_timestamp"],item["opportunity_id"])):
        key=(row["instrument"],row["strategy"]);entry=datetime.fromisoformat(row["entry_timestamp"])
        if entry < blocked_until.get(key,datetime.min.replace(tzinfo=timezone.utc)):continue
        accepted.add(row["opportunity_id"]);blocked_until[key]=datetime.fromisoformat(row["exit_timestamp"])
    return accepted


def _metrics(rows: list[dict[str, Any]], value_key: str="net_R") -> dict[str, Any]:
    values=[float(row[value_key]) for row in rows];wins=[x for x in values if x>1e-12];losses=[x for x in values if x<-1e-12]
    return {"n":len(values),"total_R":sum(values),"expectancy_R":mean(values) if values else None,"median_R":median(values) if values else None,
            "win_rate":len(wins)/len(values) if values else None,"profit_factor":sum(wins)/-sum(losses) if losses else None}


def _trimmed(values: list[float], fraction: float) -> float | None:
    if not values:return None
    count=int(len(values)*fraction);ordered=sorted(values);selected=ordered[count:len(values)-count] if count and len(values)>2*count else ordered
    return mean(selected) if selected else None


def _tail(values: list[float], fraction: float) -> float | None:
    if not values or not sum(values):return None
    count=max(1,math.ceil(len(values)*fraction));return sum(sorted(values,reverse=True)[:count])/sum(values)


def _bootstrap_day(rows: list[dict[str,Any]],seed:int,samples:int=1000)->dict[str,Any]:
    days=defaultdict(list)
    for row in rows:days[row["entry_timestamp"][:10]].append(float(row["reference_gross_R"]))
    if len(days)<3:return {"status":"insufficient_sample","days":len(days),"lower":None,"upper":None}
    blocks=[days[key] for key in sorted(days)];rng=random.Random(seed);results=[]
    for _ in range(samples):
        sampled=[rng.choice(blocks) for _ in blocks];flat=[x for block in sampled for x in block];results.append(mean(flat))
    results.sort();return {"status":"ok","days":len(days),"samples":samples,"seed":seed,"lower":results[int(.025*samples)],"upper":results[min(samples-1,int(.975*samples))]}


def build_signal_study(dataset:Path,instrument:str,strategy:str="all",seed:int=17,starting_equity:float=100_000.,risk_fraction:float=.0025,execution_profile:str="research_default",multipliers:tuple[float,...]=DEFAULT_MULTIPLIERS,output_root:Path=Path("data/reports/signal_studies"),config:ContinuousConfig|None=None)->dict[str,Any]:
    opportunities,candles,context=build_opportunities(dataset,instrument,strategy,seed,config);index_by_time={row.close_time.isoformat():index for index,row in enumerate(candles)};trades=[row for item in opportunities if (row:=resolve_canonical_trade(item,candles,index_by_time)) is not None]
    profile=get_execution_profile(instrument,execution_profile);non_overlap=non_overlapping_ids(trades);checksum=file_checksum(dataset);commit,dirty=_git_state()
    identity_payload={"version":__version__,"commit":commit,"dataset":checksum,"instrument":instrument,"market_profile":get_market_profile(instrument).profile_version,"regime":REGIME_VERSION,"strategies":["1.0"],"canonical":CANONICAL_MODEL_VERSION,"risk":"unit_stop_R_1.0","execution":profile.to_dict(),"multipliers":multipliers,"seed":seed,"config":context["configuration_checksum"]}
    study_id=sha256(json.dumps(identity_payload,sort_keys=True).encode()).hexdigest()[:20];directory=output_root/study_id
    if (directory/"study.json").exists():return {"study_id":study_id,"output_directory":str(directory),"summary":json.loads((directory/"summary.json").read_text())}
    directory.mkdir(parents=True,exist_ok=False);overlays=[apply_friction_overlay(row,profile,m,starting_equity,risk_fraction) for row in trades for m in multipliers]
    _write_jsonl(directory/"opportunities.jsonl",[asdict(row) for row in opportunities]);_write_jsonl(directory/"canonical_trades.jsonl",trades);_write_csv(directory/"overlays.csv",overlays)
    families=[family.value for family in StrategyFamily if family not in {StrategyFamily.NONE,StrategyFamily.MACRO_EVENT}]
    summary_rows=[];subgroups=[];daily=[]
    for family in families:
        canonical=[row for row in trades if row["strategy"]==family];gross=[float(row["reference_gross_R"]) for row in canonical]
        one=[row for row in overlays if row["strategy"]==family and row["cost_multiplier"]==1.0];gross_metrics=_metrics([{**row,"net_R":row["reference_gross_R"]} for row in one]);default_metrics=_metrics(one);default_cost=mean([row["total_cost_R"] for row in one]) if one else None;gross_expectancy=gross_metrics["expectancy_R"]
        break_even=gross_expectancy if gross_expectancy is not None and gross_expectancy>0 else None;entry_bps=mean([row["initial_unit_risk"]/row["entry_price"]*10_000 for row in canonical]) if canonical else None
        boot=_bootstrap_day(canonical,seed)
        record={"instrument":instrument,"strategy":family,"opportunities":len(canonical),"non_overlap":sum(row["opportunity_id"] in non_overlap for row in canonical),"gross_total_R":sum(gross),"gross_expectancy_R":gross_expectancy,"median_R":median(gross) if gross else None,"profit_factor":gross_metrics["profit_factor"],"win_rate":gross_metrics["win_rate"],"default_cost_R_per_trade":default_cost,"default_net_expectancy_R":default_metrics["expectancy_R"],"break_even_cost_R":break_even,"break_even_bps":break_even*entry_bps if break_even is not None and entry_bps is not None else None,"break_even_fraction_default":break_even/default_cost if break_even is not None and default_cost else None,"trimmed_mean_1pct":_trimmed(gross,.01),"trimmed_mean_5pct":_trimmed(gross,.05),"top_1pct_contribution":_tail(gross,.01),"top_5pct_contribution":_tail(gross,.05),"top_10pct_contribution":_tail(gross,.10),"largest_winner_R":max(gross) if gross else None,"largest_loser_R":min(gross) if gross else None,"bootstrap":boot}
        day_rows=_daily_rows(canonical,one,instrument,family);daily.extend(day_rows);record["positive_day_fraction"]=sum(row["gross_R"]>0 for row in day_rows)/len(day_rows) if day_rows else None
        day_values=[row["gross_R"] for row in day_rows];day_total=sum(day_values)
        record["best_day_R"]=max(day_values) if day_values else None;record["worst_day_R"]=min(day_values) if day_values else None
        for count in (1,3,5):record[f"best_{count}_days_contribution"]=sum(sorted(day_values,reverse=True)[:count])/day_total if day_values and day_total else None
        summary_rows.append(record)
        subgroups.extend(_subgroup_rows(canonical,instrument,family))
    scenario={}
    for multiplier in multipliers:scenario[str(multiplier)]={family:_metrics([row for row in overlays if row["strategy"]==family and row["cost_multiplier"]==multiplier]) for family in families}
    summary={"instrument":instrument,"opportunities":len(opportunities),"canonical_trades":len(trades),"non_overlap":len(non_overlap),"overlap_count":len(trades)-len(non_overlap),"strategies":summary_rows,"cost_scenarios":scenario,"candidate_reason_counts":dict(Counter(row["reason_code"] for row in context["candidates"])),"warning":"Inspected development data; signal research and synthetic friction do not establish live, broker-level, future, or holdout profitability."}
    study={"schema_version":"1.0","study_id":study_id,"feline_version":__version__,"git_commit":commit,"repository_dirty":dirty,"dataset_path":str(dataset.resolve()),"dataset_checksum":checksum,"instrument":instrument,"start":candles[0].close_time.isoformat() if candles else None,"end":candles[-1].close_time.isoformat() if candles else None,"bars":len(candles),"provider":candles[0].source if candles else None,"market_profile":get_market_profile(instrument).to_dict(),"execution_profile":profile.to_dict(),"regime_version":REGIME_VERSION,"feature_version":CONTINUOUS_ENGINE_VERSION,"canonical_model_version":CANONICAL_MODEL_VERSION,"strategy_versions":{"trend_pullback":"1.0","range_mean_reversion":"1.0","volatility_breakout":"1.0"},"configuration":context["configuration"],"configuration_checksum":context["configuration_checksum"],"starting_equity":starting_equity,"risk_fraction":risk_fraction,"display_R_usd":starting_equity*risk_fraction,"friction_multipliers":multipliers,"seed":seed,"created_at":datetime.now(timezone.utc).isoformat(),"semantics":{"entry":"decision-time completed-candle close; known at signal_available_at","exit":"subsequent OHLC stop/gap then 15-completed-bar time exit; no same-signal-bar hindsight","overlap":"all opportunities primary; non-overlap suppresses within instrument+strategy until canonical exit only"}}
    (directory/"study.json").write_text(json.dumps(study,indent=2)+"\n");(directory/"summary.json").write_text(json.dumps(summary,indent=2)+"\n");_write_csv(directory/"summary.csv",summary_rows);_write_csv(directory/"subgroup_analysis.csv",subgroups);_write_csv(directory/"daily_stability.csv",daily)
    (directory/"summary.md").write_text(_summary_markdown(study,summary_rows,scenario));(directory/"selectivity_diagnostics.md").write_text(_selectivity_markdown(summary_rows,subgroups,daily))
    return {"study_id":study_id,"output_directory":str(directory),"summary":summary}


def _daily_rows(canonical,one,instrument,family):
    gross=defaultdict(list);net=defaultdict(list)
    for row in canonical:gross[row["entry_timestamp"][:10]].append(row["reference_gross_R"])
    for row in one:net[row["entry_timestamp"][:10]].append(row["net_R"])
    return [{"instrument":instrument,"strategy":family,"date":day,"opportunities":len(values),"gross_R":sum(values),"zero_cost_net_R":sum(values),"default_net_R":sum(net[day]),"expectancy_R":mean(values),"win_rate":sum(x>0 for x in values)/len(values)} for day,values in sorted(gross.items())]


def _bucket(value):
    if value is None:return "missing"
    value=abs(float(value));return "low" if value<.0001 else "medium" if value<.001 else "high"


def _subgroup_rows(canonical,instrument,family):
    rows=[]
    dimensions={"direction":lambda r:r["direction"],"regime":lambda r:r["regime"],"session":lambda r:r.get("session"),"utc_hour":lambda r:str(r.get("utc_hour")),"volatility":lambda r:_bucket(r["predictors"].get("realized_vol_15m")),"trend_strength":lambda r:_bucket(r["predictors"].get("trend_slope_30m")),"zscore":lambda r:_bucket(r["predictors"].get("price_zscore_30m")),"range_position":lambda r:"low" if (r["predictors"].get("position_in_30m_range") or .5)<.33 else "high" if (r["predictors"].get("position_in_30m_range") or .5)>.67 else "middle"}
    for dimension,keyfn in dimensions.items():
        groups=defaultdict(list)
        for row in canonical:groups[str(keyfn(row))].append(float(row["reference_gross_R"]))
        for key,values in sorted(groups.items()):rows.append({"instrument":instrument,"strategy":family,"dimension":dimension,"bucket":key,"n":len(values),"gross_total_R":sum(values),"gross_expectancy_R":mean(values),"median_R":median(values)})
    return rows


def compare_signal_studies(paths:Iterable[Path],basis:str="native",output_root:Path=Path("data/reports/signal_studies/comparisons"))->dict[str,Any]:
    studies=[]
    for path in paths:
        directory=path if path.is_dir() else path.parent;study=json.loads((directory/"study.json").read_text());summary=json.loads((directory/"summary.json").read_text());trades=[json.loads(line) for line in (directory/"canonical_trades.jsonl").read_text().splitlines() if line];studies.append((directory,study,summary,trades))
    if len(studies)<2:raise ValueError("signals compare requires at least two studies")
    common=None
    if basis=="common":
        sets=[]
        for _,study,_,_ in studies:
            sets.append({row.close_time.isoformat() for row in read_mixed_events(Path(study["dataset_path"])) if isinstance(row,CandleUpdate) and row.instrument==study["instrument"] and row.timeframe=="1m"})
        common=set.intersection(*sets)
    elif basis!="native":raise ValueError("comparison basis must be native or common")
    rows=[]
    for directory,study,summary,trades in studies:
        for record in summary["strategies"]:
            row=dict(record)
            if common is not None:
                selected=[t for t in trades if t["entry_timestamp"] in common and t["strategy"]==row["strategy"]];gross=[{**t,"net_R":t["reference_gross_R"]} for t in selected];gross_metrics=_metrics(gross);profile=get_execution_profile(study["instrument"],study["execution_profile"]["profile_name"]);one=[apply_friction_overlay(t,profile,1,study["starting_equity"],study["risk_fraction"]) for t in selected];net_metrics=_metrics(one);cost=mean([t["total_cost_R"] for t in one]) if one else None;gross_expectancy=gross_metrics["expectancy_R"]
                boot=_bootstrap_day(selected,study["seed"]);days=_daily_rows(selected,one,study["instrument"],row["strategy"]);values=[t["reference_gross_R"] for t in selected]
                row.update({"opportunities":len(selected),"non_overlap":len(non_overlapping_ids(selected)),"gross_total_R":gross_metrics["total_R"],"gross_expectancy_R":gross_expectancy,"median_R":gross_metrics["median_R"],"profit_factor":gross_metrics["profit_factor"],"win_rate":gross_metrics["win_rate"],"default_cost_R_per_trade":cost,"default_net_expectancy_R":net_metrics["expectancy_R"],"break_even_cost_R":gross_expectancy if gross_expectancy is not None and gross_expectancy>0 else None,"break_even_bps":gross_expectancy*10 if gross_expectancy is not None and gross_expectancy>0 else None,"break_even_fraction_default":gross_expectancy/cost if gross_expectancy is not None and gross_expectancy>0 and cost else None,"common_opportunities":len(selected),"trimmed_mean_1pct":_trimmed(values,.01),"trimmed_mean_5pct":_trimmed(values,.05),"top_5pct_contribution":_tail(values,.05),"positive_day_fraction":sum(day["gross_R"]>0 for day in days)/len(days) if days else None,"bootstrap":boot})
            else:row["common_opportunities"]=None
            row["study_id"]=study["study_id"];row["comparison_basis"]=basis;row["bootstrap_lower"]=row["bootstrap"].get("lower");row["bootstrap_upper"]=row["bootstrap"].get("upper");row.pop("bootstrap",None);rows.append(row)
    comparison_id=sha256(json.dumps({"studies":[item[1]["study_id"] for item in studies],"basis":basis},sort_keys=True).encode()).hexdigest()[:20];output=output_root/comparison_id;output.mkdir(parents=True,exist_ok=True)
    payload={"schema_version":"1.0","comparison_id":comparison_id,"basis":basis,"studies":[item[1]["study_id"] for item in studies],"rows":rows,"warning":"Inspected development data; no live or future profitability claim."};(output/"comparison.json").write_text(json.dumps(payload,indent=2)+"\n");_write_csv(output/"comparison.csv",rows);(output/"comparison.md").write_text(_comparison_markdown(payload));(output/"selectivity_diagnostics.md").write_text(_selectivity_markdown(rows,[],[]));return {"comparison_id":comparison_id,"output_directory":str(output),"rows":rows}


def _write_jsonl(path,rows):path.write_text("".join(json.dumps(row,separators=(",",":"),sort_keys=True)+"\n" for row in rows))
def _write_csv(path,rows):
    if not rows:path.write_text("");return
    fields=[]
    for row in rows:
        for key in row:
            if key not in fields:fields.append(key)
    with path.open("w",newline="",encoding="utf-8") as handle:writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader();writer.writerows([{k:json.dumps(v,sort_keys=True) if isinstance(v,(dict,list)) else v for k,v in row.items()} for row in rows])


def _summary_markdown(study,rows,scenarios):
    lines=[f"# Signal-locked study — {study['instrument']}","","Predictive signal research; not a stateful portfolio backtest.","","| Strategy | Opportunities | Non-overlap | Gross R | Gross E[R] | Median R | Default E[R] |", "|---|---:|---:|---:|---:|---:|---:|"]
    for r in rows:lines.append(f"| {r['strategy']} | {r['opportunities']} | {r['non_overlap']} | {r['gross_total_R']:.6g} | {r['gross_expectancy_R']} | {r['median_R']} | {r['default_net_expectancy_R']} |")
    lines.extend(["","Cost multipliers apply to spread, slippage, commission, and financing attribution on the same canonical trades. 0x equals gross.","","These are inspected development data with synthetic friction assumptions. They do not establish live, broker-level, future, or holdout profitability."])
    return "\n".join(lines)+"\n"


def _selectivity_markdown(rows,subgroups,daily):
    lines=["# Selectivity diagnostics","","Descriptive only: no threshold search or fitted rule was performed.",""]
    for row in rows:lines.extend([f"## {row['instrument']} / {row['strategy']}",f"- Tail: top 5% contribution {row.get('top_5pct_contribution')}; 5% trimmed mean {row.get('trimmed_mean_5pct')}",f"- Daily stability: positive-day fraction {row.get('positive_day_fraction')}",f"- Bootstrap day-block CI: {row.get('bootstrap', [row.get('bootstrap_lower'),row.get('bootstrap_upper')])}",""])
    lines.append("Subgroup tables by direction, session, hour, volatility, trend strength, z-score, and range position are in `subgroup_analysis.csv`. No best bin is selected.")
    return "\n".join(lines)+"\n"


def _comparison_markdown(payload):
    lines=["# Signal-locked multi-market comparison","",f"Basis: **{payload['basis']}**","","| Market | Strategy | Opportunities | Non-overlap | Gross R | Gross E[R] | Median | PF | Win rate | Default cost R | Default net E[R] | Break-even R | Break-even bps | BE/default | Positive days | Bootstrap CI | Top 5% |","|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|"]
    for r in payload["rows"]:lines.append(f"| {r['instrument']} | {r['strategy']} | {r['opportunities']} | {r['non_overlap']} | {r['gross_total_R']} | {r['gross_expectancy_R']} | {r['median_R']} | {r['profit_factor']} | {r['win_rate']} | {r['default_cost_R_per_trade']} | {r['default_net_expectancy_R']} | {r['break_even_cost_R']} | {r['break_even_bps']} | {r['break_even_fraction_default']} | {r['positive_day_fraction']} | [{r['bootstrap_lower']}, {r['bootstrap_upper']}] | {r['top_5pct_contribution']} |")
    lines.extend(["","Inspected development data and synthetic friction assumptions; no live, broker-level, future, or holdout profitability claim."]);return "\n".join(lines)+"\n"
