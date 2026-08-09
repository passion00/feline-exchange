import unittest

from feline.config import RiskConfig
from feline.core.events import OrderRequest, PriceTick, Side
from feline.portfolio.models import Position
from feline.risk.engine import RiskEngine


def order(quantity=1, stop=99):
    return OrderRequest(instrument="TEST", side=Side.BUY, quantity=quantity, expected_price=100, stop_price=stop)


class RiskTests(unittest.TestCase):
    def setUp(self):
        self.quote = PriceTick(instrument="TEST", bid=99.9, ask=100.1)

    def test_position_limit(self):
        engine = RiskEngine(RiskConfig(max_position_size=10))
        self.assertEqual(engine.approve_order(order(6), self.quote, {"TEST": Position("TEST", 5, 100)}).rule, "position_size")

    def test_loss_per_trade(self):
        engine = RiskEngine(RiskConfig(max_loss_per_trade=5))
        self.assertEqual(engine.approve_order(order(10, 90), self.quote, {}).rule, "loss_per_trade")

    def test_spread_limit(self):
        engine = RiskEngine(RiskConfig(max_allowed_spread=0.0001))
        self.assertEqual(engine.approve_order(order(), self.quote, {}).rule, "spread")

    def test_daily_loss_activates_kill_switch(self):
        engine = RiskEngine(RiskConfig(max_daily_loss=100))
        engine.update_account(daily_pnl=-101, equity=999, peak_equity=1000)
        self.assertTrue(engine.kill_switch)
        self.assertEqual(engine.approve_order(order(), self.quote, {}).rule, "kill_switch")

    def test_drawdown_and_manual_kill_switch(self):
        engine = RiskEngine(RiskConfig(max_drawdown=0.10))
        engine.update_account(daily_pnl=0, equity=890, peak_equity=1000)
        self.assertTrue(engine.kill_switch)
        engine.reset_kill_switch()
        engine.activate_kill_switch()
        self.assertFalse(engine.approve_order(order(), self.quote, {}).approved)

    def test_valid_order_is_approved(self):
        engine = RiskEngine(RiskConfig())
        self.assertTrue(engine.approve_order(order(), self.quote, {}).approved)

