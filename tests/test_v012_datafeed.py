from __future__ import annotations

import io,json,tempfile,unittest,urllib.error
from datetime import datetime,timedelta,timezone
from pathlib import Path

from feline.core.events import PriceTick
from feline.market.datafeed import (DataFeedRegistry,HistoricalRequest,HTTPPolicy,
    ProviderRequestError,RealtimeIntegrityGuard,RetryingHTTPClient)
from feline.market.oanda import OandaV20Provider
from feline.replay.mixed import read_mixed_events
from feline.replay.native_data import DukascopyHistoricalProvider
from feline.research.market_data import audit_dataset_provenance,inspect_continuous_dataset

UTC=timezone.utc


class Response(io.BytesIO):
    def __enter__(self):return self
    def __exit__(self,*args):self.close()


class ProductionDataFeedTests(unittest.TestCase):
    def test_registry_is_interchangeable(self):
        registry=DataFeedRegistry();provider=DukascopyHistoricalProvider(Path("cache"));registry.register("dukascopy",provider)
        self.assertIs(registry.historical("DUKASCOPY"),provider)
        with self.assertRaises(ValueError):registry.historical("missing")

    def test_historical_request_boundaries_and_timezone(self):
        with self.assertRaises(ValueError):HistoricalRequest("EURUSD",datetime(2024,1,1),datetime(2024,1,2))
        with self.assertRaises(ValueError):HistoricalRequest("EURUSD",datetime(2024,1,2,tzinfo=UTC),datetime(2024,1,1,tzinfo=UTC))

    def test_oanda_completed_unsmoothed_candle_normalization(self):
        payload={"instrument":"EUR_USD","candles":[{"complete":True,"volume":7,"time":"2024-07-15T12:00:00Z","mid":{"o":"1.0900","h":"1.0910","l":"1.0890","c":"1.0905"}},
            {"complete":False,"volume":1,"time":"2024-07-15T12:01:00Z","mid":{"o":"1","h":"1","l":"1","c":"1"}}]}
        calls=[]
        def opener(request,timeout):calls.append(request.full_url);return Response(json.dumps(payload).encode())
        provider=OandaV20Provider("secret",client=RetryingHTTPClient(HTTPPolicy(retries=1,minimum_interval_seconds=0),opener))
        with tempfile.TemporaryDirectory() as td:
            output=Path(td)/"eurusd.jsonl";start=datetime(2024,7,15,12,tzinfo=UTC)
            result=provider.acquire(HistoricalRequest("EURUSD",start,start+timedelta(minutes=1)),output)
            rows=read_mixed_events(output);self.assertEqual(result.rows,1);self.assertEqual(rows[0].close_time,start+timedelta(minutes=1))
            self.assertIn("smooth=false",calls[0]);self.assertNotIn("secret",calls[0])
            self.assertEqual(inspect_continuous_dataset(output,"EURUSD",start,start+timedelta(minutes=1)).quality_status,"PASS")

    def test_oanda_rejects_malformed_envelope(self):
        client=RetryingHTTPClient(HTTPPolicy(retries=1,minimum_interval_seconds=0),lambda *a,**k:Response(b'{}'))
        provider=OandaV20Provider("secret",client=client);start=datetime(2024,7,15,tzinfo=UTC)
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):provider.acquire(HistoricalRequest("EURUSD",start,start+timedelta(minutes=1)),Path(td)/"x.jsonl")

    def test_oanda_cache_avoids_second_request(self):
        payload={"instrument":"EUR_USD","candles":[{"complete":True,"volume":1,"time":"2024-07-15T00:00:00Z","mid":{"o":"1.1","h":"1.1","l":"1.1","c":"1.1"}}]};calls=[]
        def opener(*args,**kwargs):calls.append(1);return Response(json.dumps(payload).encode())
        provider=OandaV20Provider("secret",client=RetryingHTTPClient(HTTPPolicy(retries=1,minimum_interval_seconds=0),opener));start=datetime(2024,7,15,tzinfo=UTC)
        with tempfile.TemporaryDirectory() as td:
            output=Path(td)/"x.jsonl";request=HistoricalRequest("EURUSD",start,start+timedelta(minutes=1))
            provider.acquire(request,output);provider.acquire(request,output);self.assertEqual(len(calls),1)

    def test_retry_is_bounded_and_sanitized(self):
        count=0
        def fail(*args,**kwargs):
            nonlocal count;count+=1;raise urllib.error.URLError("secret-host-detail")
        client=RetryingHTTPClient(HTTPPolicy(retries=2,base_backoff_seconds=0,minimum_interval_seconds=0),fail)
        with self.assertRaises(ProviderRequestError) as caught:client.json(__import__('urllib').request.Request("https://example.invalid/?token=SECRET"))
        self.assertEqual(count,2);self.assertNotIn("SECRET",str(caught.exception))

    def test_realtime_guard_stale_crossed_future_and_ordering(self):
        now=datetime(2024,1,1,12,tzinfo=UTC);guard=RealtimeIntegrityGuard(timedelta(seconds=5),lambda:now)
        good=PriceTick(timestamp=now,instrument="EURUSD",bid=1.1,ask=1.1001);self.assertIs(guard.validate(good),good)
        with self.assertRaises(ValueError):guard.validate(good)
        with self.assertRaises(ValueError):RealtimeIntegrityGuard(timedelta(seconds=5),lambda:now).validate(PriceTick(timestamp=now-timedelta(seconds=6),instrument="EURUSD",bid=1,ask=1.1))
        with self.assertRaises(ValueError):RealtimeIntegrityGuard(now=lambda:now).validate(PriceTick(timestamp=now,instrument="EURUSD",bid=2,ask=1))

    def test_audit_detects_checksum_tampering(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"x.jsonl";path.write_text("x")
            path.with_suffix(".jsonl.provenance.json").write_text(json.dumps({"provider":"fixture","processed_sha256":"wrong"}))
            path.with_suffix(".jsonl.quality.json").write_text(json.dumps({"quality_status":"PASS","sha256":"wrong"}))
            result=audit_dataset_provenance(path);self.assertFalse(result["ok"]);self.assertIn("processed_checksum_mismatch",result["issues"])


if __name__=="__main__":unittest.main()
