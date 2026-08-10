from __future__ import annotations

import hashlib
import json
import lzma
import struct
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from feline.market.profiles import get_execution_profile, get_market_profile
from feline.replay.native_data import (aggregate_ticks, parse_binance_kline_csv,
    parse_dukascopy_bi5, verify_binance_checksum)
from feline.research.market_data import (DatasetQualityStatus,
    assert_dataset_research_eligible, inspect_continuous_dataset)

UTC=timezone.utc


def candle(opened, instrument="BTCUSDT", o=100, h=101, lo=99, c=100):
    closed=opened.replace(second=0,microsecond=0)+__import__("datetime").timedelta(minutes=1)
    return {"type":"candle","timestamp":closed.isoformat(),"instrument":instrument,"timeframe":"1m",
            "open_time":opened.isoformat(),"close_time":closed.isoformat(),"open":o,"high":h,"low":lo,"close":c,
            "volume":1,"source":"fixture","provenance":"native"}


class NativeProviderTests(unittest.TestCase):
    def test_btcusdt_is_distinct_continuous_and_uncalibrated(self):
        profile=get_market_profile("BTCUSDT")
        self.assertEqual(profile.instrument,"BTCUSDT");self.assertNotEqual(profile.instrument,get_market_profile("BTCUSD").instrument)
        self.assertFalse(profile.is_expected_closed(datetime(2024,2,10,12,tzinfo=UTC)))
        execution=get_execution_profile("BTCUSDT");self.assertFalse(execution.calibrated);self.assertEqual(execution.spread_value,5.0)

    def test_dukascopy_bid_decode_and_aggregation(self):
        hour=datetime(2024,2,5,tzinfo=UTC)
        raw=struct.pack(">IIIff",1000,110002,110000,2.0,3.0)+struct.pack(">IIIff",59000,110012,110010,4.0,5.0)
        ticks=parse_dukascopy_bi5(lzma.compress(raw),hour,"EURUSD")
        self.assertEqual(ticks[0][0],datetime(2024,2,5,0,0,1,tzinfo=UTC));self.assertAlmostEqual(ticks[0][1],1.1)
        rows=aggregate_ticks(ticks,"EURUSD");self.assertEqual(len(rows),1);self.assertEqual(rows[0]["low"],1.1)
        self.assertEqual(rows[0]["provider_metadata"]["price_basis"],"BID")

    def test_dukascopy_malformed_rejected(self):
        with self.assertRaises((lzma.LZMAError,ValueError)):parse_dukascopy_bi5(b"bad",datetime(2024,1,1,tzinfo=UTC),"EURUSD")

    def test_binance_parser_preserves_symbol_time_and_metadata(self):
        content=b"1707091200000,43000,43100,42900,43050,12,1707091259999,516000,42,6,258000,0\n"
        row=parse_binance_kline_csv(content)[0]
        self.assertEqual(row["instrument"],"BTCUSDT");self.assertEqual(row["open_time"],"2024-02-05T00:00:00+00:00")
        self.assertEqual(row["provider_metadata"]["trade_count"],42);self.assertEqual(row["volume"],12)

    def test_binance_checksum(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"a.zip";path.write_bytes(b"archive");checksum=path.with_suffix(".zip.CHECKSUM")
            digest=hashlib.sha256(b"archive").hexdigest();checksum.write_text(f"{digest}  a.zip\n")
            self.assertEqual(verify_binance_checksum(path,checksum),digest)
            checksum.write_text("0"*64+"  a.zip\n")
            with self.assertRaises(ValueError):verify_binance_checksum(path,checksum)

    def test_quality_rejects_ohlc_without_repair(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"bad.jsonl";row=candle(datetime(2024,2,5,tzinfo=UTC),h=99)
            original=json.dumps(row)+"\n";path.write_text(original)
            quality=inspect_continuous_dataset(path,"BTCUSDT")
            self.assertEqual(quality.quality_status,DatasetQualityStatus.REJECTED.value);self.assertEqual(path.read_text(),original)

    def test_quality_detects_duplicate_and_research_gate(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"dupe.jsonl";row=candle(datetime(2024,2,5,tzinfo=UTC));path.write_text(json.dumps(row)+"\n"+json.dumps(row)+"\n")
            report=path.with_suffix(".jsonl.quality.json");quality=inspect_continuous_dataset(path,"BTCUSDT",report_path=report)
            self.assertTrue(quality.duplicate_timestamps);self.assertEqual(quality.quality_status,"REJECTED")
            with self.assertRaises(ValueError):assert_dataset_research_eligible(path)

    def test_btc_gap_is_review_not_closure_and_end_is_exclusive(self):
        from datetime import timedelta
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"gap.jsonl";start=datetime(2024,2,10,tzinfo=UTC)
            rows=[candle(start),candle(start+timedelta(minutes=2))];path.write_text("".join(json.dumps(r)+"\n" for r in rows))
            quality=inspect_continuous_dataset(path,"BTCUSDT",start,start+timedelta(minutes=2))
            self.assertEqual(quality.unexpected_missing_minutes,1);self.assertEqual(quality.quality_status,"REJECTED")
            self.assertTrue(quality.out_of_window)

    def test_clean_quality_is_deterministic(self):
        from datetime import timedelta
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"ok.jsonl";start=datetime(2024,2,5,tzinfo=UTC)
            path.write_text("".join(json.dumps(candle(start+timedelta(minutes=i)))+"\n" for i in range(3)))
            one=inspect_continuous_dataset(path,"BTCUSDT").to_dict();two=inspect_continuous_dataset(path,"BTCUSDT").to_dict()
            self.assertEqual(one,two);self.assertEqual(one["quality_status"],"PASS")


if __name__=="__main__":unittest.main()
