import csv
import json
import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from feline.core.events import CandleUpdate
from feline.macro.events import NormalizedEconomicEvent, ShockState
from feline.research.continuous import (
    ContinuousConfig, ContinuousFeatureEngine, ContinuousRegime,
    ContinuousRegimeEngine, ContinuousSnapshot, StrategyFamily,
    StrategyRouter, run_continuous_experiment, utc_session,
)
from feline.strategy.macro_event import MacroEventStrategy

UTC=timezone.utc


def candle(index,price,*,start=datetime(2024,1,8,0,0,tzinfo=UTC),complete=True,timeframe="1m",spread=.0001):
    close_time=start+timedelta(minutes=index);duration={"1m":1,"5m":5,"15m":15,"1h":60}[timeframe]
    return CandleUpdate(timestamp=close_time,instrument="EURUSD",timeframe=timeframe,open_time=close_time-timedelta(minutes=duration),close_time=close_time,
        open=price-spread/4,high=price+spread,low=price-spread,close=price,volume=0,complete=complete,source="fixture",provenance="native")


def snapshot(**overrides):
    values={"critical_event_window_active":False,"volatility_ratio_short_long":1.0,"realized_vol_5m":.0002,
      "trend_slope_30m":0.0,"price_vs_ma_60m":0.0,"range_30m":.002,"return_5m":0.,"price_zscore_30m":0.,
      "breakout_above_prior_30m_high":False,"breakout_below_prior_30m_low":False}
    values.update(overrides);now=datetime(2024,1,8,12,tzinfo=UTC)
    return ContinuousSnapshot("EURUSD",now,values,{key:now for key in values},False)


class ContinuousFeatureTests(unittest.TestCase):
    def test_incomplete_future_and_higher_timeframe_rejected(self):
        engine=ContinuousFeatureEngine()
        with self.assertRaises(ValueError):engine.update(candle(1,1.1,complete=False))
        with self.assertRaises(ValueError):engine.update(candle(5,1.1,timeframe="5m"))
        now=datetime(2024,1,1,tzinfo=UTC)
        with self.assertRaises(ValueError):ContinuousSnapshot("EURUSD",now,{"future":1},{"future":now+timedelta(seconds=1)})

    def test_features_deterministic_zero_safe_and_labels_absent(self):
        def build():
            engine=ContinuousFeatureEngine();result=None
            for index in range(70):result=engine.update(candle(index,1.1))
            return result
        first,second=build(),build();self.assertEqual(first.features,second.features)
        self.assertIsNone(first.features["volatility_ratio_short_long"]);self.assertEqual(first.features["position_in_30m_range"],.5)
        self.assertFalse(any(name.startswith("label_") for name in first.predictor_columns()))

    def test_gap_resets_and_warmup(self):
        engine=ContinuousFeatureEngine()
        for index in range(61):result=engine.update(candle(index,1.1+index*.00001))
        self.assertFalse(result.insufficient_history)
        result=engine.update(candle(200,1.2));self.assertTrue(result.insufficient_history);self.assertIsNone(result.features["return_5m"])
        decision=StrategyRouter().route(result,ContinuousRegimeEngine().classify(result));self.assertEqual(decision.signal,"NO_TRADE")

    def test_session_taxonomy_utc(self):
        expected={1:"ASIA",8:"LONDON",13:"LONDON_NEW_YORK_OVERLAP",18:"NEW_YORK",23:"OFF_HOURS"}
        for hour,value in expected.items():self.assertEqual(utc_session(datetime(2024,1,1,hour,tzinfo=UTC)),value)

    def test_event_proximity_and_override(self):
        event=NormalizedEconomicEvent("e","fed","US","rate","FOMC",datetime(2024,1,8,1,tzinfo=UTC),importance="critical",instruments=("EURUSD",))
        engine=ContinuousFeatureEngine(ContinuousConfig(minimum_history=1));result=engine.update(candle(50,1.1),[event])
        self.assertTrue(result.features["critical_event_window_active"]);self.assertEqual(ContinuousRegimeEngine().classify(result).regime,ContinuousRegime.EVENT_RISK)


class RegimeAndRouterTests(unittest.TestCase):
    def setUp(self):self.engine=ContinuousRegimeEngine();self.router=StrategyRouter()

    def test_required_regimes(self):
        cases=[
          (snapshot(trend_slope_30m=.00004,price_vs_ma_60m=.001),ContinuousRegime.TRENDING_UP),
          (snapshot(trend_slope_30m=-.00004,price_vs_ma_60m=-.001),ContinuousRegime.TRENDING_DOWN),
          (snapshot(),ContinuousRegime.RANGING),
          (snapshot(volatility_ratio_short_long=2.,realized_vol_5m=.0004),ContinuousRegime.VOLATILITY_EXPANSION),
          (snapshot(volatility_ratio_short_long=.3),ContinuousRegime.VOLATILITY_COMPRESSION),
          (snapshot(trend_slope_30m=.00003,price_vs_ma_60m=-.001,range_30m=.01),ContinuousRegime.UNCERTAIN),
          (snapshot(critical_event_window_active=True),ContinuousRegime.EVENT_RISK)]
        for state,expected in cases:
            with self.subTest(expected=expected):self.assertEqual(self.engine.classify(state).regime,expected)

    def test_strategy_regime_eligibility_and_deterministic_routing(self):
        trend=snapshot(return_5m=-.0005,trend_slope_30m=.00004,price_vs_ma_60m=.001);regime=self.engine.classify(trend)
        first=self.router.route(trend,regime);second=StrategyRouter().route(trend,regime)
        self.assertEqual(first,second);self.assertEqual(first.strategy_family,StrategyFamily.TREND_PULLBACK);self.assertEqual(first.signal,"BUY")
        ranged=snapshot(price_zscore_30m=1.1);decision=self.router.route(ranged,self.engine.classify(ranged));self.assertEqual(decision.strategy_family,StrategyFamily.RANGE_MEAN_REVERSION);self.assertEqual(decision.signal,"SELL")

    def test_breakout_requires_prior_compression_and_completed_confirmation(self):
        router=StrategyRouter();compressed=snapshot(volatility_ratio_short_long=.3);router.route(compressed,self.engine.classify(compressed))
        expanded=snapshot(volatility_ratio_short_long=2.,realized_vol_5m=.0004,breakout_above_prior_30m_high=True)
        self.assertEqual(router.route(expanded,self.engine.classify(expanded)).signal,"BUY")
        self.assertEqual(StrategyRouter().route(expanded,self.engine.classify(expanded)).signal,"NO_TRADE")

    def test_event_risk_and_position_conflict_suppress(self):
        event=snapshot(critical_event_window_active=True);decision=self.router.route(event,self.engine.classify(event));self.assertTrue(decision.suppressed);self.assertEqual(decision.strategy_family,StrategyFamily.MACRO_EVENT)
        ranged=snapshot(price_zscore_30m=1.2);decision=self.router.route(ranged,self.engine.classify(ranged),position_open=True);self.assertTrue(decision.suppressed);self.assertEqual(decision.signal,"NO_TRADE")

    def test_macro_strategy_semantics_unchanged(self):
        decision=MacroEventStrategy().evaluate(.0005,.001,ShockState.STABILIZED,.0002)
        self.assertEqual(decision.outcome.value,"no_trade");self.assertEqual(decision.reason,"insufficient_move")


class ContinuousExperimentTests(unittest.TestCase):
    def _dataset(self,root,with_event=True):
        path=root/"continuous.jsonl";rows=[];start=datetime(2024,1,8,0,tzinfo=UTC)
        for index in range(100):
            item=candle(index,1.1+index*.00002+((index%3)-1)*.00001,start=start)
            rows.append({"type":"candle","timestamp":item.close_time.isoformat(),"open_time":item.open_time.isoformat(),"close_time":item.close_time.isoformat(),"instrument":"EURUSD","timeframe":"1m","open":item.open,"high":item.high,"low":item.low,"close":item.close,"volume":0,"source":"fixture","provenance":"native"})
        if with_event:rows.append({"type":"economic","timestamp":(start+timedelta(minutes=65)).isoformat(),"id":"fomc","source":"fed","region":"US","event_type":"rate","title":"FOMC","importance":"critical","instruments":["EURUSD"]})
        rows.sort(key=lambda row:row["timestamp"]);path.write_text("".join(json.dumps(row)+"\n" for row in rows));return path

    def test_experiment_outputs_provenance_labels_and_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);dataset=self._dataset(root);first=run_continuous_experiment(dataset,output_root=root/"out",no_trades=True);second=run_continuous_experiment(dataset,output_root=root/"out",no_trades=True)
            self.assertEqual(first["experiment_id"],second["experiment_id"]);directory=Path(first["output_directory"])
            experiment=json.loads((directory/"experiment.json").read_text());self.assertEqual(experiment["dataset_checksum"],file_hash(dataset));self.assertEqual(experiment["regime_version"],"1.0")
            schema=json.loads((directory/"observation_schema.json").read_text());self.assertTrue(set(schema["label_columns"]).isdisjoint(schema["predictor_columns"]))
            with (directory/"observations.csv").open() as handle:rows=list(csv.DictReader(handle))
            self.assertEqual(len(rows),100);self.assertNotEqual(rows[0]["label_forward_return_5m"],"");self.assertEqual(rows[-1]["label_forward_return_5m"],"")
            self.assertIn("EVENT_RISK",{row["regime"] for row in rows})

    def test_mixed_replay_transitions_out_of_event_risk(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);result=run_continuous_experiment(self._dataset(root),output_root=root/"out",no_trades=True);directory=Path(result["output_directory"])
            with (directory/"regimes.csv").open() as handle:transitions=[row["current"] for row in csv.DictReader(handle)]
            self.assertIn("EVENT_RISK",transitions);self.assertNotEqual(transitions[-1],"EVENT_RISK")

    def test_eligible_orders_pass_risk_before_paper_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);path=root/"range.jsonl";start=datetime(2024,1,8,tzinfo=UTC);rows=[]
            for index in range(130):
                item=candle(index,1.1+math.sin(index/4)*.0005,start=start)
                rows.append({"type":"candle","timestamp":item.close_time.isoformat(),"open_time":item.open_time.isoformat(),"close_time":item.close_time.isoformat(),"instrument":"EURUSD","timeframe":"1m","open":item.open,"high":item.high,"low":item.low,"close":item.close,"volume":0,"source":"fixture","provenance":"native"})
            path.write_text("".join(json.dumps(row)+"\n" for row in rows));config=ContinuousConfig(compression_volatility_ratio=0.,expansion_volatility_ratio=100.,trend_min_slope_per_minute=1.,ranging_max_slope_per_minute=.001,ranging_max_range=.01,range_entry_zscore=.5)
            result=run_continuous_experiment(path,output_root=root/"out",config=config);directory=Path(result["output_directory"])
            with (directory/"signals.csv").open() as handle:signals=list(csv.DictReader(handle))
            self.assertIn("approved",{row["risk_result"] for row in signals});self.assertGreater(result["summary"]["trades"],0)


def file_hash(path):
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__=="__main__":unittest.main()
