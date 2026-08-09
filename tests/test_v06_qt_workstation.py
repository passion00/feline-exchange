import asyncio,time,unittest
from feline.gui.controller import ChartBuffer,EventProjection,ReplayController,ReplayState,RuntimeThread
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
