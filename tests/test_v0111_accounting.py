import csv
import json
import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from feline.core.events import FillEvent,Side
from feline.research.accounting import calculate_trade_accounting,classify_net_pnl,directional_excursions,validate_trade_accounting
from feline.research.continuous import ContinuousConfig,run_continuous_experiment

UTC=timezone.utc


def fill(side,mid,*,spread=0.,slippage=0.,commission=0.,quantity=1.,minute=0):
    reference=mid+spread/2 if side is Side.BUY else mid-spread/2
    actual=reference+slippage if side is Side.BUY else reference-slippage
    return FillEvent(order_id=f"o-{minute}",instrument="EURUSD",side=side,quantity=quantity,reference_price=reference,fill_price=actual,gross_value=actual*quantity,commission=commission,spread_cost=spread/2*quantity,slippage_amount=slippage*quantity,slippage_percentage=slippage/reference if reference else 0,latency_ms=0,assumptions={"spread":spread},timestamp=datetime(2024,1,1,tzinfo=UTC)+timedelta(minutes=minute))


class AccountingContractTests(unittest.TestCase):
    def assert_reconciles(self,value):
        validate_trade_accounting(value);self.assertAlmostEqual(value.reference_gross_pnl-value.spread_costs-value.slippage_costs-value.commissions-value.financing_costs,value.net_pnl,12);self.assertAlmostEqual(value.execution_pnl-value.commissions-value.financing_costs,value.net_pnl,12)

    def test_long_and_short_profit_zero_cost(self):
        long=calculate_trade_accounting(fill(Side.BUY,1.1000),fill(Side.SELL,1.1010,minute=1));short=calculate_trade_accounting(fill(Side.SELL,1.1010),fill(Side.BUY,1.1000,minute=1))
        for value in (long,short):self.assertAlmostEqual(value.reference_gross_pnl,.001);self.assertAlmostEqual(value.execution_pnl,.001);self.assertAlmostEqual(value.net_pnl,.001);self.assert_reconciles(value)

    def test_spread_once_long_and_short(self):
        for entry_side,exit_side,entry,exit in ((Side.BUY,Side.SELL,1.1,1.101),(Side.SELL,Side.BUY,1.101,1.1)):
            value=calculate_trade_accounting(fill(entry_side,entry,spread=.0002),fill(exit_side,exit,spread=.0002,minute=1));self.assertAlmostEqual(value.spread_costs,.0002);self.assertAlmostEqual(value.execution_pnl,.0008);self.assertAlmostEqual(value.net_pnl,.0008);self.assert_reconciles(value)

    def test_slippage_once_long_and_short(self):
        for entry_side,exit_side,entry,exit in ((Side.BUY,Side.SELL,1.1,1.101),(Side.SELL,Side.BUY,1.101,1.1)):
            value=calculate_trade_accounting(fill(entry_side,entry,slippage=.00005),fill(exit_side,exit,slippage=.00005,minute=1));self.assertAlmostEqual(value.slippage_costs,.0001);self.assertAlmostEqual(value.execution_pnl,.0009);self.assertAlmostEqual(value.net_pnl,.0009);self.assert_reconciles(value)

    def test_spread_slippage_commission_financing_once(self):
        value=calculate_trade_accounting(fill(Side.BUY,1.1,spread=.0002,slippage=.00005,commission=.00001),fill(Side.SELL,1.101,spread=.0002,slippage=.00005,commission=.00001,minute=1),financing_costs=.00003)
        self.assertAlmostEqual(value.reference_gross_pnl,.001);self.assertAlmostEqual(value.execution_pnl,.0007);self.assertAlmostEqual(value.net_pnl,.00065);self.assert_reconciles(value)

    def test_old_failure_pattern_is_not_double_counted(self):
        spread=.00021545999999994514;slippage=.00023700599559983715
        value=calculate_trade_accounting(fill(Side.SELL,1.0775,spread=spread,slippage=slippage/2),fill(Side.BUY,1.0771,spread=spread,slippage=slippage/2,minute=15))
        self.assertAlmostEqual(value.reference_gross_pnl,.0004,12);self.assertAlmostEqual(value.execution_pnl,-.0000524659955998,12);self.assertAlmostEqual(value.net_pnl,-.0000524659955998,12);self.assertNotAlmostEqual(value.net_pnl,-.0005049319911996,12);self.assert_reconciles(value)

    def test_classification_tolerance(self):
        self.assertEqual(classify_net_pnl(.001),"winner");self.assertEqual(classify_net_pnl(-.001),"loser");self.assertEqual(classify_net_pnl(1e-13),"breakeven")


class ExcursionTests(unittest.TestCase):
    def test_long_excursions_and_no_future(self):
        mae,mfe=directional_excursions(1.1,Side.BUY,[1.101,1.102],[1.099,1.100]);self.assertLessEqual(mae,0);self.assertGreaterEqual(mfe,0);self.assertAlmostEqual(mfe,1.102/1.1-1)
        no_mae,no_mfe=directional_excursions(1.1,Side.BUY,[1.1,1.099],[1.099,1.098]);self.assertEqual(no_mfe,0);self.assertLess(no_mae,0)
        # A later extreme is absent from the supplied in-trade interval and cannot leak in.
        self.assertLess(mfe,.01)

    def test_short_excursions_symmetric_and_nonnegative_mfe(self):
        mae,mfe=directional_excursions(1.1,Side.SELL,[1.101,1.1],[1.099,1.098]);self.assertLessEqual(mae,0);self.assertGreaterEqual(mfe,0);self.assertAlmostEqual(mfe,(1.1-1.098)/1.1)
        no_mae,no_mfe=directional_excursions(1.1,Side.SELL,[1.101,1.102],[1.1,1.101]);self.assertEqual(no_mfe,0);self.assertLess(no_mae,0)


class AggregateAccountingTests(unittest.TestCase):
    def test_strategy_and_portfolio_aggregates_reconcile(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);path=root/"range.jsonl";start=datetime(2024,1,8,tzinfo=UTC);rows=[]
            for index in range(130):
                t=start+timedelta(minutes=index);price=1.1+math.sin(index/4)*.0005
                rows.append({"type":"candle","timestamp":t.isoformat(),"open_time":(t-timedelta(minutes=1)).isoformat(),"close_time":t.isoformat(),"instrument":"EURUSD","timeframe":"1m","open":price,"high":price+.0001,"low":price-.0001,"close":price,"volume":0,"source":"fixture","provenance":"native"})
            path.write_text("".join(json.dumps(row)+"\n" for row in rows));config=ContinuousConfig(compression_volatility_ratio=0.,expansion_volatility_ratio=100.,trend_min_slope_per_minute=1.,ranging_max_slope_per_minute=.001,ranging_max_range=.01,range_entry_zscore=.5)
            result=run_continuous_experiment(path,output_root=root/"out",config=config);summary=result["summary"];portfolio=summary["portfolio"];strategies=summary["strategies"]
            self.assertGreater(summary["trades"],0);self.assertAlmostEqual(sum(value["net_pnl"] for value in strategies.values()),portfolio["net_pnl"]);self.assertAlmostEqual(portfolio["reference_gross_pnl"]-portfolio["spread_costs"]-portfolio["slippage_costs"]-portfolio["commissions"]-portfolio["financing_costs"],portfolio["net_pnl"]);self.assertAlmostEqual(portfolio["execution_pnl"]-portfolio["commissions"]-portfolio["financing_costs"],portfolio["net_pnl"])
            with open(Path(result["output_directory"])/"trades.csv") as handle:
                trades=list(csv.DictReader(handle));self.assertTrue(all(float(row["mae"])<=0 and float(row["mfe"])>=0 for row in trades))


if __name__=="__main__":unittest.main()
