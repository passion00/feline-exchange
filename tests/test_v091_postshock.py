from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest

from feline.macro.events import ShockState
from feline.research.postshock import calculate_post_shock, decision_diagnostics
from feline.strategy.macro_event import MacroEventStrategy


EVENT = datetime(2024, 9, 18, 18, 0, tzinfo=timezone.utc)


def market(stable=1.01, final=1.02, low=None, high=None, minutes=65):
    rows=[{"timestamp":EVENT.isoformat(),"close":1.,"high":1.,"low":1.}]
    for minute in range(1,minutes+1):
        close=stable if minute<=5 else stable+(final-stable)*(minute-5)/max(1,minutes-5)
        rows.append({"timestamp":(EVENT+timedelta(minutes=minute)).isoformat(),"close":close,"high":high if high is not None and minute==10 else close,"low":low if low is not None and minute==11 else close})
    return rows


class PostShockMetricTests(unittest.TestCase):
 def calculate(self, **kwargs):
    rows=kwargs.pop("rows",market(**{k:kwargs.pop(k) for k in list(kwargs) if k in {"stable","final","low","high","minutes"}}))
    return calculate_post_shock(rows,EVENT,kwargs.pop("stabilization_time",EVENT+timedelta(minutes=5)),**kwargs)

 def test_upward_and_downward_continuation(self):
    up=self.calculate(stable=1.01,final=1.03);down=self.calculate(stable=.99,final=.97)
    self.assertEqual(up["post_stabilization_outcome"],"CONTINUATION")
    self.assertEqual(down["post_stabilization_outcome"],"CONTINUATION")
    self.assertGreater(up["post_stabilization_horizons"]["15"]["return_value"],0)
    self.assertLess(down["post_stabilization_horizons"]["15"]["return_value"],0)

 def test_symmetric_mean_reversion_full_and_beyond(self):
    up=self.calculate(stable=1.02,final=.99);down=self.calculate(stable=.98,final=1.01)
    self.assertEqual(up["post_stabilization_outcome"],"MEAN_REVERSION")
    self.assertEqual(down["post_stabilization_outcome"],"MEAN_REVERSION")
    full=self.calculate(stable=1.02,final=1.)
    self.assertAlmostEqual(full["retracement_fraction"],1.)
    self.assertAlmostEqual(full["impulse_retention_fraction"],0.)
    self.assertGreater(up["retracement_fraction"],1.)
    self.assertLess(up["impulse_retention_fraction"],0.)

 def test_extension_flat_and_no_stabilization(self):
    extension=self.calculate(stable=1.01,final=1.04)
    self.assertGreater(extension["maximum_post_stabilization_extension"],0)
    flat=self.calculate(stable=1.01,final=1.0105,flat_tolerance=.001)
    self.assertEqual(flat["post_stabilization_outcome"],"FLAT")
    missing=self.calculate(stabilization_time=None)
    self.assertEqual(missing["post_stabilization_outcome"],"NO_STABILIZATION")
    self.assertEqual(missing["post_stabilization_horizons"],{})

 def test_incremental_returns_are_one_minute_anchored(self):
    rows=market(stable=1.01,final=1.02);result=self.calculate(rows=rows)
    one=next(x for x in rows if x["timestamp"]==(EVENT+timedelta(minutes=1)).isoformat())
    five=next(x for x in rows if x["timestamp"]==(EVENT+timedelta(minutes=5)).isoformat())
    fifteen=next(x for x in rows if x["timestamp"]==(EVENT+timedelta(minutes=15)).isoformat())
    self.assertAlmostEqual(result["incremental_horizons"]["5"]["return_value"],five["close"]/one["close"]-1)
    self.assertAlmostEqual(result["incremental_horizons"]["15"]["return_value"],fifteen["close"]/one["close"]-1)

 def test_native_ohlc_directional_mae_mfe(self):
    up=self.calculate(stable=1.01,final=1.02,low=1.005,high=1.03)
    row=up["post_stabilization_horizons"]["15"]
    self.assertAlmostEqual(row["mae"],1.005/1.01-1)
    self.assertAlmostEqual(row["mfe"],1.03/1.01-1)
    down=self.calculate(stable=.99,final=.98,low=.96,high=1.005)
    row=down["post_stabilization_horizons"]["15"]
    self.assertAlmostEqual(row["mfe"],-(.96/.99-1))
    self.assertAlmostEqual(row["mae"],-(1.005/.99-1))

 def test_interval_specific_contamination(self):
    secondary=SimpleNamespace(event_id="press",scheduled_timestamp=EVENT+timedelta(minutes=30),importance="critical")
    result=self.calculate(secondary_events=(secondary,))
    self.assertEqual(result["post_stabilization_horizons"]["15"]["status"],"clean")
    self.assertEqual(result["post_stabilization_horizons"]["30"]["status"],"contains_secondary_event")

 def test_no_trade_diagnostics_and_strategy_decisions_unchanged(self):
    strategy=MacroEventStrategy()
    before=strategy.evaluate(.0008,.001,ShockState.STABILIZED,.001)
    diagnostics=decision_diagnostics(.0008,.001,"stabilized",.001)
    self.assertEqual(before.reason,"insufficient_move")
    self.assertEqual(next(x for x in diagnostics if x["gate"]=="initial_move"),{"gate":"initial_move","observed_value":.0008,"raw_value":.0008,"threshold":.001,"comparison":">=","passed":False})
    unstable=strategy.evaluate(.01,.01,ShockState.SHOCK,.001)
    unstable_diagnostics=decision_diagnostics(.01,.01,"shock",.001)
    self.assertEqual(unstable.reason,"unstable_market")
    self.assertFalse(unstable_diagnostics[0]["passed"])

 def test_lookahead_and_unavailable_horizons(self):
    rows=market(minutes=10);result=self.calculate(rows=rows)
    self.assertIn("5",result["post_stabilization_horizons"])
    self.assertNotIn("15",result["post_stabilization_horizons"])
    truncated=calculate_post_shock(rows[:1],EVENT,None)
    self.assertIsNone(truncated["one_minute_reference"])
