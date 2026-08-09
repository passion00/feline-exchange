import asyncio,json,tempfile,threading,unittest
from dataclasses import replace
from datetime import date,datetime,time,timedelta,timezone
from pathlib import Path
from urllib.request import urlopen,Request

from feline.config import PaperConfig
from feline.core.events import CandleUpdate,OrderRequest,OrderStatus,OrderType,PriceTick,Regime,Side,SignalEvent
from feline.execution.models import FinancingRule
from feline.execution.paper import PaperBroker
from feline.market.calendar import ExchangeCalendar,FXCalendar
from feline.market.candles import CandleAggregator,GapPolicy
from feline.metrics import create_metrics_server
from feline.market.orchestrator import ProviderOrchestrator
from feline.portfolio.allocator import PortfolioAllocator
from feline.replay.engine import CSVReplayProvider
from feline.research.analytics import ExcursionTracker,stress_resample
from feline.research.experiments import parameter_grid,walk_forward_windows
from feline.research.stress import shock_ticks
from feline.storage.database import Database
from feline.runtime import FelineRuntime
from feline.config import AppConfig

class V03Tests(unittest.IsolatedAsyncioTestCase):
 async def test_bid_ask_commission_and_fill_audit(self):
  b=PaperBroker(config=PaperConfig(slippage_bps=0,spread_slippage_factor=0,flat_commission=1));q=PriceTick(instrument="X",bid=99,ask=101,volume=10);b.update_quote(q)
  u=await b.submit_order(OrderRequest(instrument="X",side=Side.BUY,quantity=1,expected_price=100));self.assertEqual(u.fill_price,101);self.assertEqual(b.fills[0].commission,1);self.assertEqual(b.fills[0].spread_cost,1)

 async def test_limit_partial_fill_then_complete(self):
  b=PaperBroker(config=PaperConfig(slippage_bps=0,liquidity_fraction=.5));q=PriceTick(instrument="X",bid=9,ask=10,volume=4);b.update_quote(q)
  u=await b.submit_order(OrderRequest(instrument="X",side=Side.BUY,quantity=5,expected_price=10,order_type=OrderType.LIMIT,limit_price=10));self.assertEqual(u.status,OrderStatus.PARTIALLY_FILLED);self.assertEqual(u.remaining_quantity,3)
  u=(await b.process_quote(replace(q,id="q2",timestamp=q.timestamp+timedelta(seconds=1))))[0];self.assertEqual(u.filled_quantity,4)
  u=(await b.process_quote(replace(q,id="q3",timestamp=q.timestamp+timedelta(seconds=2))))[0];self.assertEqual(u.status,OrderStatus.FILLED);self.assertEqual(b.positions["X"].quantity,5)

 async def test_latency_moves_fill_to_later_quote(self):
  b=PaperBroker(config=PaperConfig(slippage_bps=0,spread_slippage_factor=0,fixed_latency_ms=1000));t=datetime.now(timezone.utc);b.update_quote(PriceTick(timestamp=t,instrument="X",bid=99,ask=100))
  u=await b.submit_order(OrderRequest(timestamp=t,instrument="X",side=Side.BUY,quantity=1,expected_price=100));self.assertEqual(u.status,OrderStatus.ACCEPTED)
  self.assertEqual(await b.process_quote(PriceTick(timestamp=t+timedelta(milliseconds=500),instrument="X",bid=109,ask=110)),[])
  u=(await b.process_quote(PriceTick(timestamp=t+timedelta(seconds=1),instrument="X",bid=119,ask=120)))[0];self.assertEqual(u.fill_price,120)

 async def test_financing_deterministic(self):
  b=PaperBroker();b.positions["EURUSD"]=__import__('feline.portfolio.models',fromlist=['Position']).Position("EURUSD",10,1.2);b.financing_rules["EURUSD"]=FinancingRule("EURUSD",long_daily_rate=.01,triple_day=2)
  events=b.apply_financing(datetime(2026,1,7,tzinfo=timezone.utc));self.assertEqual(events[0].days,3);self.assertAlmostEqual(events[0].amount,-.36)

 async def test_pending_and_protective_atomic_recovery(self):
  with tempfile.TemporaryDirectory() as d:
   db=Database(Path(d)/"x.db");b=PaperBroker();q=PriceTick(instrument="X",bid=9,ask=10);b.update_quote(q);await b.submit_order(OrderRequest(instrument="X",side=Side.BUY,quantity=1,expected_price=10,stop_price=8,take_profit_price=12));await b.submit_order(OrderRequest(instrument="Y",side=Side.BUY,quantity=2,expected_price=5,order_type=OrderType.LIMIT,limit_price=4));db.persist_broker_state(b);recovered=db.recover_broker_state();self.assertEqual(len(recovered[2]),1);self.assertEqual(recovered[3]["X"],(8,12));db.close()

 async def test_calendars_and_gap_policy(self):
  self.assertFalse(FXCalendar().is_open(datetime(2026,1,3,12,tzinfo=timezone.utc)));cal=ExchangeCalendar("UTC",time(9),time(17),frozenset({date(2026,1,1)}));self.assertFalse(cal.is_open(datetime(2026,1,1,10,tzinfo=timezone.utc)))
  agg=CandleAggregator(("1m",),GapPolicy.FORWARD_FILL);base=datetime(2026,1,1,tzinfo=timezone.utc);agg.update(PriceTick(timestamp=base,instrument="X",bid=1,ask=1));out=agg.update(PriceTick(timestamp=base+timedelta(minutes=3),instrument="X",bid=2,ask=2));self.assertEqual(len(out),3);self.assertEqual(out[1].tick_count,0)

 async def test_multi_replay_globally_ordered(self):
  ticks=[x async for x in CSVReplayProvider(Path("tests/fixtures/multi_ticks.csv")).stream()];self.assertEqual({x.instrument for x in ticks},{"EURUSD","GBPUSD"});self.assertEqual([x.timestamp for x in ticks],sorted(x.timestamp for x in ticks))

 async def test_allocator_experiments_walkforward_excursions_stress(self):
  s=SignalEvent(instrument="X",side=Side.BUY,strength=1,strategy="x",price=10);self.assertLessEqual(PortfolioAllocator().allocate(s,1000,1000,0),25)
  self.assertEqual(len(list(parameter_grid({"a":[1,2],"b":[3,4]},3))),3);windows=walk_forward_windows(list(range(10)),4,2);self.assertEqual(windows[0],(0,3,4,5));self.assertLess(windows[0][1],windows[0][2])
  tracker=ExcursionTracker(100);tracker.update(110,90);self.assertEqual((tracker.mae,tracker.mfe),(10,10));self.assertEqual(stress_resample([.1,-.2],10,7)["iterations"],10);self.assertEqual(len(shock_ticks("fx_5pct")),21)

 async def test_metrics_read_only(self):
  try:server=create_metrics_server(lambda:{"health":"ok"})
  except PermissionError:self.skipTest("sandbox forbids loopback socket binding")
  thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();url=f"http://127.0.0.1:{server.server_port}/health";self.assertEqual(json.load(urlopen(url))["health"],"ok")
  with self.assertRaises(Exception):urlopen(Request(url,data=b"x",method="POST"));server.shutdown();server.server_close()

 async def test_force_close_and_provider_isolation(self):
  with tempfile.TemporaryDirectory() as d:
   r=FelineRuntime(AppConfig(database_path=str(Path(d)/"x.db")));q=PriceTick(instrument="X",bid=99,ask=100);r.broker.update_quote(q);await r.broker.submit_order(OrderRequest(instrument="X",side=Side.BUY,quantity=1,expected_price=100));policy=await r.finalize_replay("FORCE_CLOSE");self.assertEqual(policy,"FORCE_CLOSE");self.assertEqual(r.broker.positions["X"].quantity,0);r.database.close()
  states=[]
  class Bad:
   async def stream(self):
    if False:yield
    raise ConnectionError
  class Good:
   async def stream(self):yield PriceTick(instrument="X",bid=1,ask=1)
  o=ProviderOrchestrator(lambda *x:states.append(x));o.add("bad",Bad(),1);o.add("good",Good(),1);o.start();await asyncio.sleep(.01);self.assertEqual(o.queues["good"].qsize(),1);self.assertTrue(any(x[:2]==("bad","failed") for x in states));await o.stop()
