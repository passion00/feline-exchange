from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from feline.market.profiles import get_execution_profile, get_market_profile
from feline.research.risk_normalization import build_equity_curve, cost_edge_metrics, cost_sensitivity, size_position, trade_r_multiple
from feline.replay.twelvedata import convert_twelvedata_file
from feline.research.market_data import _gap_is_expected


class MarketProfileTests(unittest.TestCase):
    def test_required_profiles_and_execution_defaults(self):
        self.assertEqual(get_market_profile("EUR/USD").asset_class,"fx")
        self.assertEqual(get_market_profile("XAUUSD").asset_class,"spot_metal")
        self.assertEqual(get_market_profile("BTCUSD").trading_calendar,"CRYPTO_24_7")
        for symbol in ("EURUSD","XAUUSD","BTCUSD"):
            self.assertFalse(get_execution_profile(symbol).calibrated)
            zero=get_execution_profile(symbol,"reference_zero_cost")
            self.assertEqual((zero.spread_value,zero.base_slippage_value,zero.spread_dependent_slippage),(0,0,0))
        eur=get_execution_profile("EURUSD")
        self.assertEqual((eur.spread_value,eur.base_slippage_value,eur.spread_dependent_slippage),(2,1,.05))

    def test_calendars_are_market_specific(self):
        saturday=datetime(2024,2,10,12,tzinfo=timezone.utc)
        self.assertTrue(get_market_profile("EURUSD").is_expected_closed(saturday))
        self.assertTrue(get_market_profile("XAUUSD").is_expected_closed(saturday))
        self.assertFalse(get_market_profile("BTCUSD").is_expected_closed(saturday))

    def test_twelve_data_symbol_normalization(self):
        fixture=Path("tests/fixtures/twelvedata_sample.json")
        with tempfile.TemporaryDirectory() as temporary:
            for source,expected in (("XAU/USD","XAUUSD"),("BTC/USD","BTCUSD"),("EUR/USD","EURUSD")):
                target=Path(temporary)/f"{expected}.jsonl";convert_twelvedata_file(fixture,target,source)
                self.assertEqual(json.loads(target.read_text().splitlines()[0])["instrument"],expected)

    def test_expected_closure_differs_from_missing_data(self):
        friday=datetime(2024,2,9,22,0,tzinfo=timezone.utc);sunday=datetime(2024,2,11,22,1,tzinfo=timezone.utc)
        self.assertTrue(_gap_is_expected(friday,sunday,get_market_profile("EURUSD")))
        self.assertFalse(_gap_is_expected(friday,sunday,get_market_profile("BTCUSD")))
        self.assertFalse(_gap_is_expected(datetime(2024,2,8,10,tzinfo=timezone.utc),datetime(2024,2,8,10,2,tzinfo=timezone.utc),get_market_profile("EURUSD")))


class RiskSizingTests(unittest.TestCase):
    def test_long_short_and_multiplier(self):
        long=size_position(equity=100000,risk_fraction=.0025,entry_price=100,stop_price=99,contract_multiplier=1,maximum_notional=100000)
        short=size_position(equity=100000,risk_fraction=.0025,entry_price=100,stop_price=101,contract_multiplier=1,maximum_notional=100000)
        self.assertEqual(long.quantity,250);self.assertEqual(long.initial_risk_amount,250);self.assertEqual(short.quantity,250)
        multiplied=size_position(equity=100000,risk_fraction=.0025,entry_price=10,stop_price=9,contract_multiplier=10)
        self.assertEqual(multiplied.quantity,25);self.assertEqual(multiplied.initial_risk_amount,250)

    def test_rejection_caps_and_r(self):
        self.assertFalse(size_position(equity=100000,risk_fraction=.0025,entry_price=1,stop_price=1).accepted)
        capped=size_position(equity=100000,risk_fraction=.0025,entry_price=100,stop_price=99,maximum_notional=1000)
        self.assertEqual(capped.quantity,10);self.assertTrue(capped.capped)
        self.assertEqual(trade_r_multiple(300,250),1.2);self.assertEqual(trade_r_multiple(-300,250),-1.2)


class PortfolioAnalyticsTests(unittest.TestCase):
    def _trade(self,stamp,net,risk=100,reference=None,cost=1):
        return {"trade_id":stamp,"exit_timestamp":stamp,"net_pnl":net,"pnl_R":net/risk,"initial_risk_amount":risk,"reference_gross_pnl":net+cost if reference is None else reference,"execution_pnl":net,"spread_costs":cost,"slippage_costs":0,"commissions":0,"financing_costs":0,"quantity":1,"reference_entry_price":100}

    def test_equity_drawdown(self):
        rows,metrics=build_equity_curve([self._trade("2024-01-01",100),self._trade("2024-01-02",-250),self._trade("2024-01-03",50)],100000)
        self.assertEqual(rows[-1]["realized_equity"],99900)
        self.assertEqual(metrics["realized_max_drawdown"],-250)
        self.assertAlmostEqual(metrics["realized_max_drawdown_percent"],-250/100100)
        self.assertEqual(metrics["realized_max_drawdown_R"],-2.5)

    def test_cost_edge_break_even_and_sensitivity(self):
        trades=[self._trade("a",9,reference=10,cost=1),self._trade("b",19,reference=20,cost=1)]
        metrics=cost_edge_metrics(trades)
        self.assertEqual(metrics["reference_edge_to_cost_ratio"],15)
        self.assertEqual(metrics["break_even_average_cost_per_trade"],15)
        scenarios=cost_sensitivity(trades)
        self.assertEqual(scenarios[0]["hypothetical_net_pnl"],30)
        self.assertEqual(scenarios[4]["hypothetical_net_pnl"],28)
        self.assertIsNone(cost_edge_metrics([self._trade("c",-2,reference=-1,cost=1)])["break_even_average_cost_per_trade"])


if __name__ == "__main__": unittest.main()
