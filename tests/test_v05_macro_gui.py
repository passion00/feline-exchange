import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from feline.gui.viewmodel import DashboardViewModel
from feline.macro.events import *
from feline.replay.mixed import read_mixed_events
from feline.strategy.macro_event import MacroEventStrategy
from feline.portfolio.trades import ExitReason,TradeLifecycle

class MacroGuiTests(unittest.TestCase):
 def test_phases_shock_stabilization(self):
  t=datetime.now(timezone.utc);event=NormalizedEconomicEvent("x","fed","US","fomc","FOMC",t,importance="critical")
  self.assertEqual(event_phase(event,t-timedelta(minutes=1)),EventPhase.PRE_EVENT);self.assertEqual(event_phase(event,t),EventPhase.ANNOUNCEMENT)
  d=ShockDetector(ShockConfig(minimum_stable_samples=2));self.assertEqual(d.update(.02,.001,.002),ShockState.SHOCK);d.update(.0001,.0001,.00001);self.assertEqual(d.update(.0001,.0001,.00001),ShockState.STABILIZED)
 def test_continuation_reversion_no_trade(self):
  s=MacroEventStrategy();self.assertEqual(s.evaluate(.01,.002,ShockState.STABILIZED,.001).outcome,MacroOutcome.CONTINUATION);self.assertEqual(s.evaluate(.01,-.002,ShockState.STABILIZED,.001).outcome,MacroOutcome.MEAN_REVERSION);self.assertEqual(s.evaluate(.01,.002,ShockState.SHOCK,.001).outcome,MacroOutcome.NO_TRADE)
 def test_horizon_normalization_mixed_and_gui_separation(self):
  event=NormalizedEconomicEvent("x","ecb","EU","rate","ECB",datetime.now(timezone.utc),consensus=2,actual=2.5);self.assertEqual(event.surprise,.5);m=measure_horizon([1,1.01,1.02],[.001]*3,5);self.assertEqual(m.classification,MacroOutcome.CONTINUATION)
  events=read_mixed_events(Path("tests/fixtures/fed_macro.jsonl"));self.assertEqual(len(events),5);self.assertEqual(type(events[1]).__name__,"NormalizedEconomicEvent")
  vm=DashboardViewModel();self.assertFalse(hasattr(vm,"submit_order"));self.assertEqual(vm.state.mode,"PAPER / RESEARCH")
 def test_trade_lifecycle_mae_mfe_long_short(self):
  now=datetime.now(timezone.utc);life=TradeLifecycle();long=life.start("X","long","s","1",None,now,2,100);life.update("X",90,91);life.update("X",110,111);done=life.close("X",now+timedelta(minutes=5),105,ExitReason.TARGET,1);self.assertEqual((done.mae,done.mfe),(10,10));self.assertEqual(done.holding_seconds,300);self.assertEqual(done.net_pnl,9)
  short=life.start("Y","short","s","1",None,now,1,100);life.update("Y",89,90);life.update("Y",109,110);done=life.close("Y",now+timedelta(minutes=1),95,ExitReason.STRATEGY);self.assertEqual((done.mae,done.mfe),(10,10))
