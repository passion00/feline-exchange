from __future__ import annotations

import json, math, tempfile, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from feline.market.profiles import get_execution_profile
from feline.research.continuous import ContinuousConfig
from feline.research.signals import (SignalOpportunity, _bootstrap_day, _metrics,
    _tail, _trimmed, apply_friction_overlay, build_opportunities,
    build_signal_study, non_overlapping_ids, resolve_canonical_trade)
from feline.replay.mixed import read_mixed_events
from feline.core.events import CandleUpdate

UTC=timezone.utc


def fixture(root:Path,instrument="EURUSD",count=130):
    path=root/f"{instrument}.jsonl";start=datetime(2024,1,8,tzinfo=UTC);rows=[]
    for index in range(count):
        close=1.1+math.sin(index/4)*.0005;stamp=start+timedelta(minutes=index+1)
        rows.append({"type":"candle","timestamp":stamp.isoformat(),"open_time":(stamp-timedelta(minutes=1)).isoformat(),"close_time":stamp.isoformat(),"instrument":instrument,"timeframe":"1m","open":close,"high":close+.0001,"low":close-.0001,"close":close,"volume":0,"source":"fixture","provenance":"native"})
    path.write_text("".join(json.dumps(row)+"\n" for row in rows));return path


def config():return ContinuousConfig(compression_volatility_ratio=0.,expansion_volatility_ratio=100.,trend_min_slope_per_minute=1.,ranging_max_slope_per_minute=.001,ranging_max_range=.01,range_entry_zscore=.5)


class SignalLockTests(unittest.TestCase):
    def test_opportunity_ids_deterministic_and_predictors_have_no_outcomes(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=fixture(Path(temporary));one,_,_=build_opportunities(path,"EURUSD",config=config());two,_,_=build_opportunities(path,"EURUSD",config=config())
            self.assertEqual([x.opportunity_id for x in one],[x.opportunity_id for x in two]);self.assertTrue(one)
            self.assertTrue(all(not key.startswith("label_") for item in one for key in item.predictors))

    def test_overlay_locks_trade_and_reconciles(self):
        trade={"opportunity_id":"x","instrument":"EURUSD","strategy":"range_mean_reversion","regime":"RANGING","direction":"long","entry_timestamp":"2024-01-01T00:00:00+00:00","exit_timestamp":"2024-01-01T00:15:00+00:00","entry_price":1.1,"exit_price":1.101,"stop_reference_price":1.0989,"target_reference_price":None,"initial_unit_risk":.0011,"reference_gross_price":.001,"reference_gross_R":.001/.0011}
        zero=apply_friction_overlay(trade,get_execution_profile("EURUSD"),0);one=apply_friction_overlay(trade,get_execution_profile("EURUSD"),1)
        for key in ("opportunity_id","entry_timestamp","exit_timestamp","direction","strategy","regime","stop_reference_price","target_reference_price"):self.assertEqual(zero[key],one[key])
        self.assertAlmostEqual(zero["net_R"],zero["reference_gross_R"]);self.assertLess(one["net_R"],zero["net_R"]);self.assertAlmostEqual(one["reference_gross_R"]-one["total_cost_R"],one["net_R"])

    def test_all_multipliers_keep_ids_and_count(self):
        trade={"opportunity_id":"x","instrument":"EURUSD","strategy":"s","regime":"RANGING","direction":"short","entry_timestamp":"2024-01-01T00:00:00+00:00","exit_timestamp":"2024-01-01T00:15:00+00:00","entry_price":1.1,"exit_price":1.099,"stop_reference_price":1.1011,"target_reference_price":None,"initial_unit_risk":.0011,"reference_gross_price":.001,"reference_gross_R":.001/.0011}
        rows=[apply_friction_overlay(trade,get_execution_profile("EURUSD"),m) for m in (0,.25,.5,.75,1,1.5,2)]
        self.assertEqual({r["opportunity_id"] for r in rows},{"x"});self.assertEqual(len(rows),7);self.assertEqual(len({r["reference_gross_R"] for r in rows}),1)

    def test_reference_long_short_stop_gap_and_time(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=fixture(Path(temporary),count=30);candles=[x for x in read_mixed_events(path) if isinstance(x,CandleUpdate)];entry=candles[0].close
            base=dict(opportunity_id="x",instrument="EURUSD",dataset_checksum="d",timestamp=candles[0].close_time.isoformat(),signal_available_at=candles[0].close_time.isoformat(),regime="RANGING",strategy="range_mean_reversion",reference_price=entry,reference_entry_timestamp=candles[0].close_time.isoformat(),reference_entry_price=entry,target_reference_price=None,maximum_holding_bars=15,predictors={},eligibility={},strategy_version="1",regime_version="1",market_profile_version="1",configuration_checksum="c",seed=1)
            long=SignalOpportunity(direction="long",stop_reference_price=entry-.01,stop_distance=.01,**base);short=SignalOpportunity(direction="short",stop_reference_price=entry+.01,stop_distance=.01,**base)
            self.assertEqual(resolve_canonical_trade(long,candles)["exit_reason"],"time_exit");self.assertAlmostEqual(resolve_canonical_trade(short,candles)["reference_gross_price"],entry-candles[15].close)
            gap=dict(base);gap["stop_distance"]=.0001;gap["stop_reference_price"]=entry-.0001;g=resolve_canonical_trade(SignalOpportunity(direction="long",**gap),candles);self.assertAlmostEqual(g["reference_gross_R"],-1)

    def test_non_overlap_is_strategy_scoped_and_cost_independent(self):
        rows=[{"opportunity_id":"a","instrument":"EURUSD","strategy":"s1","entry_timestamp":"2024-01-01T00:00:00+00:00","exit_timestamp":"2024-01-01T00:10:00+00:00"},{"opportunity_id":"b","instrument":"EURUSD","strategy":"s1","entry_timestamp":"2024-01-01T00:05:00+00:00","exit_timestamp":"2024-01-01T00:15:00+00:00"},{"opportunity_id":"c","instrument":"EURUSD","strategy":"s2","entry_timestamp":"2024-01-01T00:05:00+00:00","exit_timestamp":"2024-01-01T00:15:00+00:00"},{"opportunity_id":"d","instrument":"BTCUSD","strategy":"s1","entry_timestamp":"2024-01-01T00:05:00+00:00","exit_timestamp":"2024-01-01T00:15:00+00:00"}]
        self.assertEqual(non_overlapping_ids(rows),{"a","c","d"})

    def test_metrics_tail_trim_and_bootstrap(self):
        rows=[{"net_R":x} for x in (1,2,-1)];m=_metrics(rows);self.assertAlmostEqual(m["expectancy_R"],2/3);self.assertEqual(m["median_R"],1);self.assertEqual(m["win_rate"],2/3);self.assertEqual(_trimmed(list(range(100)),.01),49.5);self.assertIsNotNone(_tail([1,2,3],.05))
        daily=[{"entry_timestamp":f"2024-01-0{i}T00:00:00+00:00","reference_gross_R":float(i)} for i in range(1,5)]
        self.assertEqual(_bootstrap_day(daily,17),_bootstrap_day(list(reversed(daily)),17));self.assertEqual(_bootstrap_day(daily[:2],17)["status"],"insufficient_sample")

    def test_study_outputs_and_overlay_counts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);path=fixture(root);result=build_signal_study(path,"EURUSD",seed=17,output_root=root/"out",config=config());directory=Path(result["output_directory"]);summary=result["summary"]
            self.assertTrue((directory/"opportunities.jsonl").exists());self.assertGreater(summary["opportunities"],0)
            counts={sum(value["n"] for value in scenario.values()) for scenario in summary["cost_scenarios"].values()};self.assertEqual(len(counts),1)
            self.assertEqual(result["study_id"],build_signal_study(path,"EURUSD",seed=17,output_root=root/"out",config=config())["study_id"])


if __name__=="__main__":unittest.main()
