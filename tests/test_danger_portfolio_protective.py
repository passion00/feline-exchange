import tempfile,unittest
from datetime import datetime,timezone
from pathlib import Path

from feline.config import RiskConfig
from feline.core.events import EconomicEvent,OrderRequest,PriceTick,Side
from feline.execution.paper import PaperBroker
from feline.portfolio.models import Position
from feline.risk.engine import RiskEngine
from feline.storage.database import Database
from feline.core.events import PortfolioSnapshot


class DangerProtectiveTests(unittest.IsolatedAsyncioTestCase):
 async def test_event_danger_blocks_new_exposure(self):
  now=datetime.now(timezone.utc); engine=RiskEngine(RiskConfig())
  engine.danger.schedule(EconomicEvent(name="Rate decision",scheduled_at=now,importance="critical"))
  req=OrderRequest(timestamp=now,instrument="X",side=Side.BUY,quantity=1,expected_price=100,stop_price=99)
  self.assertEqual(engine.approve_order(req,PriceTick(instrument="X",bid=99.9,ask=100.1),{}).rule,"event_danger")

 async def test_stop_loss_gap_has_adverse_slippage(self):
  broker=PaperBroker(slippage_bps=10,volatility_slippage_multiplier=4); broker.update_quote(PriceTick(instrument="X",bid=99.9,ask=100))
  await broker.submit_order(OrderRequest(instrument="X",side=Side.BUY,quantity=1,expected_price=100,stop_price=95,take_profit_price=110))
  broker.extreme_volatility=True
  updates=await broker.process_quote(PriceTick(instrument="X",bid=90,ask=91))
  self.assertEqual(len(updates),1); self.assertLess(updates[0].fill_price,90)

 async def test_portfolio_recovery(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/"x.db"; db=Database(path)
   snap=PortfolioSnapshot(cash=900,equity=1000,realized_pnl=2,unrealized_pnl=8,exposure=100,peak_equity=1010,drawdown=.01,trading_state="enabled",positions={"X":{"quantity":1,"average_price":100,"realized_pnl":2}})
   db.persist_event(snap); db.close(); db=Database(path); cash,positions=db.latest_portfolio(); self.assertEqual(cash,900); self.assertEqual(positions["X"].average_price,100); db.close()
