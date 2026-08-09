import unittest

from feline.core.events import OrderRequest, OrderStatus, PriceTick, Side
from feline.execution.paper import PaperBroker


class PaperBrokerTests(unittest.IsolatedAsyncioTestCase):
    async def test_fill_and_close_position(self):
        broker = PaperBroker(initial_cash=10_000, slippage_bps=0)
        broker.update_quote(PriceTick(instrument="EURUSD", bid=1.0, ask=1.01))
        filled = await broker.submit_order(OrderRequest(instrument="EURUSD", side=Side.BUY, quantity=10, expected_price=1.01, stop_price=0.99))
        self.assertEqual(filled.status, OrderStatus.FILLED)
        self.assertEqual(broker.get_positions()["EURUSD"].quantity, 10)
        closed = await broker.close_position("EURUSD")
        self.assertEqual(closed.status, OrderStatus.FILLED)
        self.assertEqual(broker.get_positions()["EURUSD"].quantity, 0)

    async def test_missing_quote_rejected(self):
        broker = PaperBroker()
        result = await broker.submit_order(OrderRequest(instrument="X", side=Side.BUY, quantity=1, expected_price=1, stop_price=0.9))
        self.assertEqual(result.status, OrderStatus.REJECTED)

