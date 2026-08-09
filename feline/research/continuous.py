from __future__ import annotations

import csv
import asyncio
import json
import math
import subprocess
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from hashlib import sha256
from pathlib import Path
from statistics import mean
from typing import Any, Iterable
from uuid import uuid4

from feline import __version__
from feline.core.events import CandleUpdate
from feline.config import PaperConfig,RiskConfig
from feline.core.events import OrderRequest,PriceTick,Side
from feline.execution.paper import PaperBroker
from feline.macro.events import NormalizedEconomicEvent
from feline.replay.mixed import read_mixed_events
from feline.replay.session_report import file_checksum
from feline.risk.engine import RiskEngine

CONTINUOUS_ENGINE_VERSION = "1.0"
REGIME_VERSION = "1.0"


class ContinuousRegime(str, Enum):
    WARMUP = "WARMUP"
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    VOLATILITY_EXPANSION = "VOLATILITY_EXPANSION"
    VOLATILITY_COMPRESSION = "VOLATILITY_COMPRESSION"
    EVENT_RISK = "EVENT_RISK"
    UNCERTAIN = "UNCERTAIN"


class StrategyFamily(str, Enum):
    MACRO_EVENT = "macro_event"
    TREND_PULLBACK = "trend_pullback"
    RANGE_MEAN_REVERSION = "range_mean_reversion"
    VOLATILITY_BREAKOUT = "volatility_breakout"
    NONE = "none"


@dataclass(frozen=True)
class ContinuousConfig:
    minimum_history: int = 61
    maximum_gap_minutes: float = 2.0
    trend_min_slope_per_minute: float = 0.000025
    trend_min_price_vs_ma: float = 0.0008
    ranging_max_slope_per_minute: float = 0.00002
    ranging_max_range: float = 0.004
    expansion_volatility_ratio: float = 1.8
    compression_volatility_ratio: float = 0.55
    minimum_activity_volatility: float = 0.00005
    pullback_min: float = 0.0001
    pullback_max: float = 0.0015
    range_entry_zscore: float = 0.8
    breakout_buffer: float = 0.0
    event_minutes_before: float = 30.0
    event_minutes_after: float = 30.0
    synthetic_spread_bps: float = 2.0


@dataclass(frozen=True)
class ContinuousFeatureValue:
    name: str
    value: Any
    available_at: datetime
    predictor: bool = True


@dataclass(frozen=True)
class ContinuousSnapshot:
    instrument: str
    decision_timestamp: datetime
    features: dict[str, Any]
    availability: dict[str, datetime]
    insufficient_history: bool = False

    def __post_init__(self) -> None:
        future = [name for name, stamp in self.availability.items() if stamp > self.decision_timestamp]
        if future:
            raise ValueError(f"continuous lookahead: {', '.join(future)}")

    def predictor_columns(self) -> tuple[str, ...]:
        return tuple(self.features)


@dataclass(frozen=True)
class RegimeDecision:
    regime: ContinuousRegime
    regime_strength: float
    reasons: tuple[str, ...]
    feature_values_used: dict[str, Any]
    decision_timestamp: datetime
    regime_version: str = REGIME_VERSION


@dataclass(frozen=True)
class StrategyDecision:
    strategy_family: StrategyFamily
    strategy_version: str
    regime_required: tuple[ContinuousRegime, ...]
    signal: str
    strength: float
    reason: str
    decision_timestamp: datetime
    eligible: bool
    suppressed: bool = False
    invalidation: str | None = None


def utc_session(timestamp: datetime) -> str:
    """Deterministic UTC session: Asia 00-07, London 07-12, overlap 12-16, NY 16-21."""
    hour = timestamp.astimezone(timezone.utc).hour
    if 0 <= hour < 7: return "ASIA"
    if 7 <= hour < 12: return "LONDON"
    if 12 <= hour < 16: return "LONDON_NEW_YORK_OVERLAP"
    if 16 <= hour < 21: return "NEW_YORK"
    return "OFF_HOURS"


def _population_volatility(values: list[float]) -> float | None:
    if len(values) < 2: return None
    returns = [values[index] / values[index - 1] - 1 for index in range(1, len(values)) if values[index - 1]]
    if not returns: return None
    center = mean(returns)
    return math.sqrt(sum((value - center) ** 2 for value in returns) / len(returns))


def _linear_slope(values: list[float]) -> float | None:
    if len(values) < 2 or not values[-1]: return None
    xbar = (len(values) - 1) / 2
    denominator = sum((index - xbar) ** 2 for index in range(len(values)))
    slope = sum((index - xbar) * (value - mean(values)) for index, value in enumerate(values)) / denominator
    return slope / values[-1]


def _zscore(values: list[float]) -> float | None:
    if len(values) < 2: return None
    center = mean(values); variance = sum((value - center) ** 2 for value in values) / len(values)
    return (values[-1] - center) / math.sqrt(variance) if variance > 0 else None


def _shape(candle: CandleUpdate) -> tuple[float, float, float, float]:
    width = candle.high - candle.low
    if width <= 0: return 0.0, 0.0, 0.0, 0.0
    return (abs(candle.close - candle.open) / width,
            (candle.high - max(candle.open, candle.close)) / width,
            (min(candle.open, candle.close) - candle.low) / width,
            width / candle.close if candle.close else 0.0)


class ContinuousFeatureEngine:
    """Incremental, completed-1m-bar feature engine with deterministic gap reset."""
    def __init__(self, config: ContinuousConfig | None = None):
        self.config = config or ContinuousConfig()
        self.history: dict[str, deque[CandleUpdate]] = defaultdict(lambda: deque(maxlen=121))
        self.last_gap_reset: dict[str, datetime] = {}

    def update(self, candle: CandleUpdate, events: Iterable[NormalizedEconomicEvent] = ()) -> ContinuousSnapshot:
        if not candle.complete:
            raise ValueError("incomplete candle cannot enter continuous features")
        if candle.timeframe != "1m":
            raise ValueError("continuous decisions require completed 1m candles")
        if candle.timestamp != candle.close_time:
            raise ValueError("continuous candle timestamp must represent completion time")
        history = self.history[candle.instrument]
        if history and candle.close_time <= history[-1].close_time:
            raise ValueError("continuous candles must be strictly chronological")
        if history and (candle.close_time - history[-1].close_time).total_seconds() > self.config.maximum_gap_minutes * 60:
            history.clear(); self.last_gap_reset[candle.instrument] = candle.close_time
        history.append(candle)
        values = list(history); close = candle.close
        features: dict[str, Any] = {}
        availability: dict[str, datetime] = {}
        def put(name: str, value: Any) -> None:
            features[name] = value; availability[name] = candle.close_time
        for minutes in (1, 5, 15, 30, 60):
            subset = values[-(minutes + 1):]
            put(f"return_{minutes}m", close / subset[0].close - 1 if len(subset) >= minutes + 1 and subset[0].close else None)
        for minutes in (5, 15, 30, 60):
            subset = values[-(minutes + 1):]
            put(f"realized_vol_{minutes}m", _population_volatility([item.close for item in subset]) if len(subset) >= minutes + 1 else None)
            range_rows = values[-minutes:]
            put(f"range_{minutes}m", (max(item.high for item in range_rows)-min(item.low for item in range_rows))/close if len(range_rows) >= minutes and close else None)
        atr_rows=values[-14:]
        put("atr_like_14m",mean(item.high-item.low for item in atr_rows)/close if len(atr_rows)>=14 and close else None)
        for minutes in (15, 30, 60):
            subset = values[-minutes:]
            prices = [item.close for item in subset]
            put(f"trend_slope_{minutes}m", _linear_slope(prices) if len(prices) >= minutes else None)
            put(f"price_vs_ma_{minutes}m", close / mean(prices) - 1 if len(prices) >= minutes and mean(prices) else None)
            put(f"price_zscore_{minutes}m", _zscore(prices) if len(prices) >= minutes else None)
        vol5, vol30 = features["realized_vol_5m"], features["realized_vol_30m"]
        range5, range30 = features["range_5m"], features["range_30m"]
        put("volatility_ratio_short_long", vol5 / vol30 if vol5 is not None and vol30 not in (None, 0) else None)
        put("range_ratio_short_long", range5 / range30 if range5 is not None and range30 not in (None, 0) else None)
        for minutes in (15, 30):
            subset = values[-minutes:]
            high, low = (max(item.high for item in subset), min(item.low for item in subset)) if len(subset) >= minutes else (None, None)
            put(f"distance_from_{minutes}m_high", (close-high)/close if high is not None and close else None)
            put(f"distance_from_{minutes}m_low", (close-low)/close if low is not None and close else None)
        prior = values[-31:-1]
        put("breakout_above_prior_30m_high", bool(prior and close > max(item.high for item in prior)) if len(prior) == 30 else None)
        put("breakout_below_prior_30m_low", bool(prior and close < min(item.low for item in prior)) if len(prior) == 30 else None)
        if len(values) >= 30:
            subset=values[-30:]; high=max(item.high for item in subset);low=min(item.low for item in subset)
            put("position_in_30m_range", (close-low)/(high-low) if high > low else None)
        else: put("position_in_30m_range", None)
        body, upper, lower, candle_range = _shape(candle)
        for name, value in zip(("candle_body_fraction","upper_wick_fraction","lower_wick_fraction","candle_range_fraction"),(body,upper,lower,candle_range)): put(name,value)
        put("utc_hour", candle.close_time.astimezone(timezone.utc).hour);put("weekday",candle.close_time.astimezone(timezone.utc).weekday());put("session",utc_session(candle.close_time))
        critical = sorted((event for event in events if event.importance.lower() in {"critical","high"}), key=lambda event:event.scheduled_at)
        future=[event for event in critical if event.scheduled_at >= candle.close_time];past=[event for event in critical if event.scheduled_at <= candle.close_time]
        put("minutes_to_next_critical_event",(future[0].scheduled_at-candle.close_time).total_seconds()/60 if future else None)
        put("minutes_since_previous_critical_event",(candle.close_time-past[-1].scheduled_at).total_seconds()/60 if past else None)
        active=any(event.scheduled_at-timedelta(minutes=self.config.event_minutes_before)<=candle.close_time<=event.scheduled_at+timedelta(minutes=self.config.event_minutes_after) for event in critical)
        put("critical_event_window_active",active)
        return ContinuousSnapshot(candle.instrument,candle.close_time,features,availability,len(values)<self.config.minimum_history)


class ContinuousRegimeEngine:
    def __init__(self, config: ContinuousConfig | None = None): self.config=config or ContinuousConfig()
    def classify(self, snapshot: ContinuousSnapshot) -> RegimeDecision:
        f=snapshot.features;used={name:f.get(name) for name in ("critical_event_window_active","volatility_ratio_short_long","realized_vol_5m","trend_slope_30m","price_vs_ma_60m","range_30m")}
        if f.get("critical_event_window_active"):return RegimeDecision(ContinuousRegime.EVENT_RISK,1.0,("critical macro-event protection window active",),used,snapshot.decision_timestamp)
        if snapshot.insufficient_history:return RegimeDecision(ContinuousRegime.WARMUP,0.0,("insufficient completed history",),used,snapshot.decision_timestamp)
        ratio=f.get("volatility_ratio_short_long");vol=f.get("realized_vol_5m")
        if ratio is not None and ratio>=self.config.expansion_volatility_ratio and (vol or 0)>=self.config.minimum_activity_volatility:
            return RegimeDecision(ContinuousRegime.VOLATILITY_EXPANSION,min(1.,ratio/self.config.expansion_volatility_ratio-0.0),("short volatility exceeds long volatility",),used,snapshot.decision_timestamp)
        if ratio is not None and ratio<=self.config.compression_volatility_ratio:
            return RegimeDecision(ContinuousRegime.VOLATILITY_COMPRESSION,min(1.,1-ratio),("short volatility compressed versus long volatility",),used,snapshot.decision_timestamp)
        slope=f.get("trend_slope_30m");location=f.get("price_vs_ma_60m")
        if slope is not None and location is not None and abs(slope)>=self.config.trend_min_slope_per_minute and abs(location)>=self.config.trend_min_price_vs_ma and slope*location>0:
            regime=ContinuousRegime.TRENDING_UP if slope>0 else ContinuousRegime.TRENDING_DOWN
            strength=min(1.,min(abs(slope)/self.config.trend_min_slope_per_minute,abs(location)/self.config.trend_min_price_vs_ma)/2)
            return RegimeDecision(regime,strength,("slope and longer price location agree",),used,snapshot.decision_timestamp)
        if slope is not None and abs(slope)<=self.config.ranging_max_slope_per_minute and (f.get("range_30m") or math.inf)<=self.config.ranging_max_range:
            return RegimeDecision(ContinuousRegime.RANGING,min(1.,1-abs(slope)/self.config.ranging_max_slope_per_minute),("low slope inside bounded normalized range",),used,snapshot.decision_timestamp)
        return RegimeDecision(ContinuousRegime.UNCERTAIN,0.0,("no deterministic regime rule satisfied",),used,snapshot.decision_timestamp)


class StrategyRouter:
    VERSION="1.0"
    def __init__(self,config:ContinuousConfig|None=None):self.config=config or ContinuousConfig();self.previous_regime:dict[str,ContinuousRegime]={}
    def route(self,snapshot:ContinuousSnapshot,regime:RegimeDecision,enabled:Iterable[str] = ("all",),position_open:bool=False)->StrategyDecision:
        allowed=set(enabled);f=snapshot.features;now=snapshot.decision_timestamp;previous=self.previous_regime.get(snapshot.instrument);self.previous_regime[snapshot.instrument]=regime.regime
        if regime.regime is ContinuousRegime.EVENT_RISK:return StrategyDecision(StrategyFamily.MACRO_EVENT,"existing",(ContinuousRegime.EVENT_RISK,),"NO_TRADE",0,"ordinary strategies suppressed by event risk",now,False,True)
        if position_open:return StrategyDecision(StrategyFamily.NONE,self.VERSION,tuple(),"NO_TRADE",0,"existing instrument position prevents conflicting strategy",now,False,True)
        if regime.regime is ContinuousRegime.WARMUP:return StrategyDecision(StrategyFamily.NONE,self.VERSION,tuple(),"NO_TRADE",0,"insufficient_history",now,False)
        def enabled(name:str)->bool:return "all" in allowed or name in allowed
        if regime.regime in {ContinuousRegime.TRENDING_UP,ContinuousRegime.TRENDING_DOWN} and enabled(StrategyFamily.TREND_PULLBACK.value):
            trend=1 if regime.regime is ContinuousRegime.TRENDING_UP else -1;pullback=-(f.get("return_5m") or 0)*trend
            eligible=self.config.pullback_min<=pullback<=self.config.pullback_max
            return StrategyDecision(StrategyFamily.TREND_PULLBACK,"1.0",(ContinuousRegime.TRENDING_UP,ContinuousRegime.TRENDING_DOWN),"BUY" if eligible and trend>0 else "SELL" if eligible else "NO_TRADE",min(1.,pullback/self.config.pullback_max) if eligible else 0,"modest counter-trend pullback" if eligible else "waiting for pullback",now,eligible,invalidation="trend regime ends")
        if regime.regime is ContinuousRegime.RANGING and enabled(StrategyFamily.RANGE_MEAN_REVERSION.value):
            z=f.get("price_zscore_30m");eligible=z is not None and abs(z)>=self.config.range_entry_zscore
            return StrategyDecision(StrategyFamily.RANGE_MEAN_REVERSION,"1.0",(ContinuousRegime.RANGING,),"SELL" if eligible and z>0 else "BUY" if eligible else "NO_TRADE",min(1.,abs(z or 0)/2),"range-edge displacement" if eligible else "price near range center",now,eligible,invalidation="range regime ends")
        if regime.regime is ContinuousRegime.VOLATILITY_EXPANSION and enabled(StrategyFamily.VOLATILITY_BREAKOUT.value):
            up=f.get("breakout_above_prior_30m_high") is True;down=f.get("breakout_below_prior_30m_low") is True;confirmed=previous is ContinuousRegime.VOLATILITY_COMPRESSION and (up or down)
            return StrategyDecision(StrategyFamily.VOLATILITY_BREAKOUT,"1.0",(ContinuousRegime.VOLATILITY_EXPANSION,),"BUY" if confirmed and up else "SELL" if confirmed and down else "NO_TRADE",regime.regime_strength if confirmed else 0,"completed breakout after compression" if confirmed else "no completed compression-breakout sequence",now,confirmed,invalidation="return inside prior range")
        return StrategyDecision(StrategyFamily.NONE,self.VERSION,tuple(),"NO_TRADE",0,"no strategy eligible for regime",now,False)


def _git_state()->tuple[str,bool]:
    try:
        commit=subprocess.run(["git","rev-parse","HEAD"],capture_output=True,text=True,check=True,timeout=2).stdout.strip()
        dirty=bool(subprocess.run(["git","status","--porcelain"],capture_output=True,text=True,timeout=2).stdout.strip());return commit,dirty
    except Exception:return "unknown",True


def run_continuous_experiment(dataset:Path,instrument:str="EURUSD",strategy:str="all",seed:int=0,no_trades:bool=False,output_root:Path=Path("data/reports/continuous"),config:ContinuousConfig|None=None)->dict[str,Any]:
    config=config or ContinuousConfig();events=read_mixed_events(dataset);candles=[event for event in events if isinstance(event,CandleUpdate) and event.instrument==instrument and event.timeframe=="1m"]
    macro=[event for event in events if isinstance(event,NormalizedEconomicEvent)];engine=ContinuousFeatureEngine(config);regimes=ContinuousRegimeEngine(config);router=StrategyRouter(config);observations=[];decisions=[];transitions=[];trades=[];previous=None
    paper=PaperBroker(config=PaperConfig(random_seed=seed,synthetic_spread_bps=config.synthetic_spread_bps));risk=RiskEngine(RiskConfig());open_trade=None
    def quote_for(candle):
        half=config.synthetic_spread_bps/20_000
        return PriceTick(timestamp=candle.close_time,instrument=instrument,bid=candle.close*(1-half),ask=candle.close*(1+half),source=f"{candle.source}:synthetic_execution")
    def execute(request,quote):
        asyncio.run(paper.process_quote(quote));approval=risk.approve_order(request,quote,paper.get_positions())
        if approval.approved:asyncio.run(paper.submit_order(request))
        return approval
    for index,candle in enumerate(candles):
        quote=quote_for(candle);asyncio.run(paper.process_quote(quote));snapshot=engine.update(candle,macro);regime=regimes.classify(snapshot)
        if open_trade and (index-open_trade["entry_index"]>=15 or regime.regime is ContinuousRegime.EVENT_RISK):
            position=paper.positions.get(instrument);side=Side.SELL if position and position.quantity>0 else Side.BUY;request=OrderRequest(instrument=instrument,side=side,quantity=abs(position.quantity),expected_price=quote.mid,stop_price=quote.mid,signal_id=open_trade["signal_id"],timestamp=candle.close_time);approval=execute(request,quote)
            if approval.approved and paper.fills:
                exit_fill=paper.fills[-1];entry_fill=open_trade["entry_fill"];direction=1 if entry_fill.side is Side.BUY else -1;gross=(exit_fill.fill_price-entry_fill.fill_price)*entry_fill.quantity*direction;related=[entry_fill,exit_fill];costs=sum(fill.commission+fill.spread_cost+fill.slippage_amount for fill in related);period=candles[open_trade["entry_index"]:index+1];adverse=min((item.low/entry_fill.fill_price-1)*direction for item in period);favorable=max((item.high/entry_fill.fill_price-1)*direction for item in period)
                trades.append({"trade_id":open_trade["signal_id"],"instrument":instrument,"strategy_family":open_trade["family"],"originating_regime":open_trade["regime"],"signal_timestamp":open_trade["timestamp"],"entry_timestamp":entry_fill.timestamp.isoformat(),"exit_timestamp":exit_fill.timestamp.isoformat(),"direction":"long" if direction>0 else "short","quantity":entry_fill.quantity,"entry_price":entry_fill.fill_price,"exit_price":exit_fill.fill_price,"gross_pnl":gross,"spread_costs":sum(fill.spread_cost for fill in related),"slippage_costs":sum(fill.slippage_amount for fill in related),"commissions":sum(fill.commission for fill in related),"net_pnl":gross-costs,"holding_seconds":(exit_fill.timestamp-entry_fill.timestamp).total_seconds(),"mae":adverse,"mfe":favorable,"exit_reason":"event_risk" if regime.regime is ContinuousRegime.EVENT_RISK else "time_exit"});open_trade=None
        decision=router.route(snapshot,regime,(strategy,),position_open=open_trade is not None);decision_row={"timestamp":candle.close_time.isoformat(),"instrument":instrument,**{key:(value.value if isinstance(value,Enum) else value) for key,value in asdict(decision).items()},"risk_result":"not_evaluated"}
        if decision.eligible and not no_trades and open_trade is None:
            side=Side.BUY if decision.signal=="BUY" else Side.SELL;stop=quote.mid*(1-.001 if side is Side.BUY else 1+.001);request=OrderRequest(instrument=instrument,side=side,quantity=1.,expected_price=quote.mid,stop_price=stop,signal_id=f"{instrument}-{index}",timestamp=candle.close_time);approval=execute(request,quote);decision_row["risk_result"]=approval.rule
            if approval.approved and paper.fills:open_trade={"entry_fill":paper.fills[-1],"entry_index":index,"family":decision.strategy_family.value,"regime":regime.regime.value,"timestamp":candle.close_time.isoformat(),"signal_id":request.signal_id}
        if regime.regime!=previous:transitions.append({"timestamp":candle.close_time.isoformat(),"instrument":instrument,"previous":previous.value if previous else None,"current":regime.regime.value,"reasons":"|".join(regime.reasons)});previous=regime.regime
        observations.append({"timestamp":candle.close_time.isoformat(),"instrument":instrument,"close":candle.close,"regime":regime.regime.value,"regime_strength":regime.regime_strength,**snapshot.features})
        decisions.append(decision_row)
    if open_trade and candles:
        candle=candles[-1];quote=quote_for(candle);position=paper.positions.get(instrument);side=Side.SELL if position and position.quantity>0 else Side.BUY;request=OrderRequest(instrument=instrument,side=side,quantity=abs(position.quantity),expected_price=quote.mid,stop_price=quote.mid,signal_id=open_trade["signal_id"],timestamp=candle.close_time);approval=execute(request,quote)
        if approval.approved and paper.fills:
            exit_fill=paper.fills[-1];entry_fill=open_trade["entry_fill"];direction=1 if entry_fill.side is Side.BUY else -1;gross=(exit_fill.fill_price-entry_fill.fill_price)*entry_fill.quantity*direction;related=[entry_fill,exit_fill];costs=sum(fill.commission+fill.spread_cost+fill.slippage_amount for fill in related);period=candles[open_trade["entry_index"]:];trades.append({"trade_id":open_trade["signal_id"],"instrument":instrument,"strategy_family":open_trade["family"],"originating_regime":open_trade["regime"],"signal_timestamp":open_trade["timestamp"],"entry_timestamp":entry_fill.timestamp.isoformat(),"exit_timestamp":exit_fill.timestamp.isoformat(),"direction":"long" if direction>0 else "short","quantity":entry_fill.quantity,"entry_price":entry_fill.fill_price,"exit_price":exit_fill.fill_price,"gross_pnl":gross,"spread_costs":sum(fill.spread_cost for fill in related),"slippage_costs":sum(fill.slippage_amount for fill in related),"commissions":sum(fill.commission for fill in related),"net_pnl":gross-costs,"holding_seconds":(exit_fill.timestamp-entry_fill.timestamp).total_seconds(),"mae":min((item.low/entry_fill.fill_price-1)*direction for item in period),"mfe":max((item.high/entry_fill.fill_price-1)*direction for item in period),"exit_reason":"replay_end"})
    closes={candle.close_time:candle.close for candle in candles}
    ordered=sorted(closes)
    for index,row in enumerate(observations):
        stamp=datetime.fromisoformat(row["timestamp"])
        for minutes in (5,15,30):row[f"label_forward_return_{minutes}m"]=closes[ordered[index+minutes]]/row["close"]-1 if index+minutes<len(ordered) and ordered[index+minutes]-stamp==timedelta(minutes=minutes) else None
    commit,dirty=_git_state();checksum=file_checksum(dataset);configuration=asdict(config);identity=sha256(json.dumps({"dataset_checksum":checksum,"instrument":instrument,"strategy":strategy,"seed":seed,"no_trades":no_trades,"config":configuration,"engine":CONTINUOUS_ENGINE_VERSION,"commit":commit},sort_keys=True).encode()).hexdigest();experiment_id=identity[:20];directory=output_root/experiment_id
    if (directory/"experiment.json").exists() and (directory/"summary.json").exists():
        return {"experiment_id":experiment_id,"output_directory":str(directory),"summary":json.loads((directory/"summary.json").read_text())}
    directory.mkdir(parents=True,exist_ok=False)
    def write_csv(name,rows):
        if not rows:(directory/name).write_text("");return
        fields=[]
        for row in rows:
            for key in row:
                if key not in fields:fields.append(key)
        with (directory/name).open("w",newline="",encoding="utf-8") as handle:writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader();writer.writerows(rows)
    write_csv("observations.csv",observations);write_csv("regimes.csv",transitions);write_csv("signals.csv",decisions);write_csv("trades.csv",trades)
    regime_counts=Counter(row["regime"] for row in observations);family_counts=Counter(row["strategy_family"] for row in decisions);signal_counts=Counter(row["signal"] for row in decisions);session_counts=defaultdict(Counter);hour_counts=defaultdict(Counter)
    for row in observations:session_counts[row["session"]][row["regime"]]+=1;hour_counts[str(row["utc_hour"])][row["regime"]]+=1
    durations=defaultdict(list);run_regime=None;run_length=0
    for row in observations:
        if row["regime"]!=run_regime:
            if run_regime is not None:durations[run_regime].append(run_length)
            run_regime=row["regime"];run_length=1
        else:run_length+=1
    if run_regime is not None:durations[run_regime].append(run_length)
    def family_metrics(family):
        selected=[trade for trade in trades if trade["strategy_family"]==family.value];wins=[trade["net_pnl"] for trade in selected if trade["net_pnl"]>0];losses=[trade["net_pnl"] for trade in selected if trade["net_pnl"]<0];gross_wins=sum(wins);gross_losses=-sum(losses)
        return {"eligible_setups":sum(row["strategy_family"]==family.value and row["eligible"] for row in decisions),"rejected_setups":sum(row["strategy_family"]==family.value and not row["eligible"] for row in decisions),"sample_size":sum(row["strategy_family"]==family.value for row in decisions),"executed_trades":len(selected),"winners":len(wins),"losers":len(losses),"win_rate":len(wins)/len(selected) if selected else None,"average_win":mean(wins) if wins else None,"average_loss":mean(losses) if losses else None,"expectancy":mean([trade["net_pnl"] for trade in selected]) if selected else None,"profit_factor":gross_wins/gross_losses if gross_losses else None,"gross_pnl":sum(trade["gross_pnl"] for trade in selected),"net_pnl":sum(trade["net_pnl"] for trade in selected),"transaction_costs":sum(trade["gross_pnl"]-trade["net_pnl"] for trade in selected),"slippage":sum(trade["slippage_costs"] for trade in selected),"commissions":sum(trade["commissions"] for trade in selected),"mae":mean([trade["mae"] for trade in selected]) if selected else None,"mfe":mean([trade["mfe"] for trade in selected]) if selected else None,"average_holding_seconds":mean([trade["holding_seconds"] for trade in selected]) if selected else None}
    summary={"bars":len(observations),"regime_counts":dict(regime_counts),"regime_transitions":len(transitions),"average_regime_duration_bars":{key:mean(value) for key,value in durations.items()},"regimes_by_session":{key:dict(value) for key,value in session_counts.items()},"regimes_by_utc_hour":{key:dict(value) for key,value in hour_counts.items()},"strategy_evaluations":dict(family_counts),"signals":dict(signal_counts),"no_trade":signal_counts.get("NO_TRADE",0),"trades":len(trades),"strategies":{family.value:family_metrics(family) for family in StrategyFamily if family is not StrategyFamily.NONE},"portfolio":{"gross_pnl":sum(trade["gross_pnl"] for trade in trades),"net_pnl":sum(trade["net_pnl"] for trade in trades),"transaction_costs":sum(trade["gross_pnl"]-trade["net_pnl"] for trade in trades),"max_drawdown":None},"baseline":{"forward_5m_mean":mean(values) if (values:=[row["label_forward_return_5m"] for row in observations if row["label_forward_return_5m"] is not None]) else None,"n":len(values)}}
    experiment={"schema_version":"1.0","experiment_id":experiment_id,"feline_version":__version__,"git_commit":commit,"repository_dirty":dirty,"dataset_path":str(dataset.resolve()),"dataset_checksum":checksum,"provider":candles[0].source if candles else None,"data_provenance":candles[0].provenance if candles else None,"instrument":instrument,"timeframe":"1m","start_timestamp":candles[0].close_time.isoformat() if candles else None,"end_timestamp":candles[-1].close_time.isoformat() if candles else None,"configuration":configuration,"configuration_checksum":sha256(json.dumps(configuration,sort_keys=True).encode()).hexdigest(),"strategy_versions":{"trend_pullback":"1.0","range_mean_reversion":"1.0","volatility_breakout":"1.0","macro_event":"unchanged"},"regime_version":REGIME_VERSION,"seed":seed,"execution_assumptions":{"orders_executed":not no_trades,"mode":"existing deterministic RiskEngine then PaperBroker" if not no_trades else "feature/regime/setup observations only","synthetic_spread_bps":config.synthetic_spread_bps,"slippage_bps":paper.config.slippage_bps},"created_at":datetime.now(timezone.utc).isoformat()}
    observation_schema={"schema_version":"1.0","predictor_columns":[name for name in observations[0] if not name.startswith("label_")] if observations else [],"label_columns":["label_forward_return_5m","label_forward_return_15m","label_forward_return_30m"],"labels_are_future_outcomes":True,"availability_rule":"all predictors use completed candles with close_time <= timestamp; labels never enter regime, routing, risk, or execution"}
    (directory/"experiment.json").write_text(json.dumps(experiment,indent=2)+"\n");(directory/"observation_schema.json").write_text(json.dumps(observation_schema,indent=2)+"\n");(directory/"summary.json").write_text(json.dumps(summary,indent=2)+"\n");(directory/"summary.md").write_text(f"# Continuous market research\n\n- Bars: {len(observations)}\n- Regimes: {dict(regime_counts)}\n- Signals: {dict(signal_counts)}\n- Executed trades: {len(trades)}\n- Gross P/L: {summary['portfolio']['gross_pnl']}\n- Costs: {summary['portfolio']['transaction_costs']}\n- Net P/L: {summary['portfolio']['net_pnl']}\n\nReference strategies are unoptimized research hypotheses. Zero-trade families remain visible. Descriptive forward returns and paper results do not establish profitability.\n")
    return {"experiment_id":experiment_id,"output_directory":str(directory),"summary":summary}
