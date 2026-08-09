import tempfile,unittest
from pathlib import Path
from feline.config import PaperConfig
from feline.core.events import OrderRequest,OrderStatus,PriceTick,Side
from feline.execution.paper import PaperBroker
from feline.execution.transitions import validate_transition
from feline.market.profiles import PROFILES
from feline.metrics import metrics_response
from feline.research.experiments import dataset_checksum,create_experiment
from feline.storage.database import Database

class IntegrityTests(unittest.IsolatedAsyncioTestCase):
 async def test_atomic_fault_injection_rolls_back_or_commits_once(self):
  for point in ("before_transaction","after_order_state","after_fill_insertion","after_cash_calculation","after_position_calculation","before_commit"):
   with tempfile.TemporaryDirectory() as d:
    db=Database(Path(d)/"x.db");b=PaperBroker(config=PaperConfig(slippage_bps=0,spread_slippage_factor=0));b.update_quote(PriceTick(instrument="X",bid=9,ask=10));u=await b.submit_order(OrderRequest(instrument="X",side=Side.BUY,quantity=1,expected_price=10))
    with self.assertRaises(RuntimeError):db.commit_execution(b,b.fills,[u],point)
    self.assertEqual(db.count("fills"),0);self.assertTrue(db.integrity_report()["ok"]);db.close()
  with tempfile.TemporaryDirectory() as d:
   db=Database(Path(d)/"x.db");b=PaperBroker();b.update_quote(PriceTick(instrument="X",bid=9,ask=10));u=await b.submit_order(OrderRequest(instrument="X",side=Side.BUY,quantity=1,expected_price=10))
   with self.assertRaises(RuntimeError):db.commit_execution(b,b.fills,[u],"after_commit")
   db.commit_execution(b,b.fills,[u]);self.assertEqual(db.count("fills"),1);self.assertTrue(db.integrity_report()["ok"]);db.close()

 async def test_transition_checksum_profiles_and_callable_metrics(self):
  validate_transition(OrderStatus.ACCEPTED,OrderStatus.PARTIALLY_FILLED)
  with self.assertRaises(ValueError):validate_transition(OrderStatus.FILLED,OrderStatus.ACCEPTED)
  checksum=dataset_checksum("tests/fixtures/sample_ticks.csv");self.assertEqual(len(checksum),64);self.assertEqual(create_experiment("reference","tests/fixtures/sample_ticks.csv",{}).dataset_checksum,checksum)
  self.assertEqual(PROFILES["EURUSD"].asset_class,"fx");self.assertEqual(metrics_response(lambda:{"ok":True},"/metrics"),(200,{"ok":True}));self.assertEqual(metrics_response(lambda:{},"/metrics","POST")[0],405)
