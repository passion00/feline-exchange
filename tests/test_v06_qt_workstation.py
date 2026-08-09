import asyncio,time,unittest
from feline.gui.controller import ChartBuffer,EventProjection,ReplayController,ReplayState,RuntimeThread
from feline.gui.controller import WorkstationController
from feline.config import AppConfig
from pathlib import Path
import tempfile
from feline.gui.viewmodel import DashboardViewModel

class WorkstationTests(unittest.TestCase):
 def test_bounded_chart_and_event_projection(self):
  chart=ChartBuffer(3)
  for i in range(5):chart.add(i,i*2)
  self.assertEqual(list(chart.points),[(2,4),(3,6),(4,8)])
  events=EventProjection(2);events.add(1,"MARKET","tick","EURUSD");events.add(2,"RISK","approved");events.add(3,"TRADE","closed")
  self.assertEqual(len(events.rows),2);self.assertEqual(events.rows[-1]["category"],"TRADE")
 def test_replay_pause_resume_stop(self):
  r=ReplayController();r.configure("x.csv","5");r.start();self.assertEqual(r.state,ReplayState.RUNNING);r.pause();self.assertEqual(r.state,ReplayState.PAUSED);r.resume();self.assertEqual(r.state,ReplayState.RUNNING);r.stop();self.assertEqual(r.state,ReplayState.STOPPED)
 def test_runtime_thread_and_gui_core_separation(self):
  controller=RuntimeThread();controller.start()
  for _ in range(50):
   if controller.loop:break
   time.sleep(.01)
  async def value():return 7
  self.assertEqual(controller.submit(value()).result(1),7);controller.stop();self.assertFalse(hasattr(DashboardViewModel(),"submit_order"))
 def test_real_runtime_replay_projection_and_restart(self):
  with tempfile.TemporaryDirectory() as d:
   c=WorkstationController(AppConfig(database_path=str(Path(d)/"gui.db")));c.start_replay("tests/fixtures/sample_ticks.csv","MAX");c.future.result(3);messages=c.drain(1000);self.assertTrue(any(x["kind"]=="tick" for x in messages));snap=c.snapshot();self.assertIn("EURUSD",snap["prices"]);self.assertIn("portfolio",snap);self.assertIn("risk",snap);c.start_replay("tests/fixtures/multi_ticks.csv","MAX");c.future.result(3);self.assertEqual(set(c.snapshot()["prices"]),{"EURUSD","GBPUSD"});c.shutdown()
