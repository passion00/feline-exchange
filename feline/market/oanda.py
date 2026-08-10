"""Read-only OANDA v20 historical and pricing-stream market-data adapter."""
from __future__ import annotations

import asyncio
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from feline.core.events import PriceTick
from feline.market.datafeed import (HistoricalDataProvider,HistoricalRequest,
    ProviderCapabilities,RealtimeDataProvider,RealtimeIntegrityGuard,RetryingHTTPClient)
from feline.replay.native_data import NativeDatasetResult,_sha,_write_native

OANDA_DATA_VERSION="1.0"


class OandaV20Provider(HistoricalDataProvider,RealtimeDataProvider):
    capabilities=ProviderCapabilities("oanda_v20",True,True,True,True,("EURUSD","XAUUSD"))
    def __init__(self,token:str|None=None,account_id:str|None=None,environment:str="practice",client:RetryingHTTPClient|None=None):
        self.token=token or os.environ.get("FELINE_OANDA_API_TOKEN");self.account_id=account_id or os.environ.get("FELINE_OANDA_ACCOUNT_ID")
        if not self.token:raise ValueError("FELINE_OANDA_API_TOKEN is required")
        self.rest_base="https://api-fxpractice.oanda.com" if environment=="practice" else "https://api-fxtrade.oanda.com"
        self.stream_base="https://stream-fxpractice.oanda.com" if environment=="practice" else "https://stream-fxtrade.oanda.com"
        self.client=client or RetryingHTTPClient();self.guard=RealtimeIntegrityGuard()

    def _request(self,url:str)->urllib.request.Request:
        return urllib.request.Request(url,headers={"Authorization":f"Bearer {self.token}","Accept-Datetime-Format":"RFC3339","User-Agent":"FelineExchange/0.12 read-only data"})

    @staticmethod
    def provider_symbol(instrument:str)->str:
        key=instrument.replace("/","").upper()
        if key not in {"EURUSD","XAUUSD"}:raise ValueError(f"OANDA FX adapter does not support {instrument}")
        return key[:3]+"_"+key[3:]

    def acquire(self,request:HistoricalRequest,output:Path):
        symbol=self.provider_symbol(request.instrument);basis={"mid":"M","bid":"B","ask":"A"}.get(request.price_basis.lower())
        if not basis:raise ValueError("OANDA price basis must be mid, bid, or ask")
        provenance_path=output.with_suffix(output.suffix+".provenance.json")
        if output.exists() and provenance_path.exists():
            cached=json.loads(provenance_path.read_text())
            if (cached.get("provider")=="oanda_v20" and cached.get("requested_symbol")==symbol and
                cached.get("requested_start")==request.start.isoformat() and cached.get("requested_end_exclusive")==request.end_exclusive.isoformat() and
                cached.get("price_basis")==request.price_basis.lower() and cached.get("processed_sha256")==_sha(output)):
                return NativeDatasetResult(str(output),str(provenance_path),int(cached["row_count"]),_sha(output),0,1,0)
        rows=[];cursor=request.start.astimezone(timezone.utc)
        while cursor<request.end_exclusive:
            finish=min(cursor+timedelta(minutes=5000),request.end_exclusive)
            query=urllib.parse.urlencode({"price":basis,"granularity":"M1","from":cursor.isoformat(),"to":finish.isoformat(),"smooth":"false","includeFirst":"true"})
            payload=self.client.json(self._request(f"{self.rest_base}/v3/instruments/{symbol}/candles?{query}"))
            if payload.get("instrument")!=symbol or not isinstance(payload.get("candles"),list):raise ValueError("malformed OANDA candle envelope")
            for item in payload["candles"]:
                if not item.get("complete"):continue
                opened=datetime.fromisoformat(item["time"].replace("Z","+00:00"));prices=item[{"M":"mid","B":"bid","A":"ask"}[basis]]
                if request.start<=opened<request.end_exclusive:
                    close=opened+timedelta(minutes=1);rows.append({"type":"candle","timestamp":close.isoformat(),"instrument":request.instrument.replace("/","").upper(),"timeframe":"1m","open_time":opened.isoformat(),"close_time":close.isoformat(),"open":float(prices["o"]),"high":float(prices["h"]),"low":float(prices["l"]),"close":float(prices["c"]),"volume":float(item.get("volume",0)),"source":"oanda_v20","provenance":"native","provider_metadata":{"provider_symbol":symbol,"price_basis":request.price_basis.lower(),"complete":True}})
            cursor=finish
        provenance={"provider":"oanda_v20","requested_symbol":symbol,"instrument":request.instrument.replace("/","").upper(),"price_basis":request.price_basis.lower(),"source_resolution":"1m_candle","output_resolution":"1m","requested_start":request.start.isoformat(),"requested_end_exclusive":request.end_exclusive.isoformat(),"source_files":[]}
        return _write_native(rows,output,provenance,0,len(rows)>0)

    async def stream(self,instruments:tuple[str,...]):
        if not self.account_id:raise ValueError("FELINE_OANDA_ACCOUNT_ID is required for realtime pricing")
        symbols=",".join(self.provider_symbol(item) for item in instruments)
        url=f"{self.stream_base}/v3/accounts/{self.account_id}/pricing/stream?"+urllib.parse.urlencode({"instruments":symbols,"snapshot":"true"})
        failures=0
        while failures<self.client.policy.retries:
            response=None
            try:
                response=await asyncio.to_thread(self.client.open,self._request(url))
                while True:
                    line=await asyncio.to_thread(response.readline)
                    if not line:raise ConnectionError("OANDA pricing stream closed")
                    row=json.loads(line)
                    if row.get("type")=="HEARTBEAT":continue
                    bids=row.get("bids") or [];asks=row.get("asks") or []
                    if not bids or not asks:continue
                    stamp=datetime.fromisoformat(row["time"].replace("Z","+00:00"));instrument=row["instrument"].replace("_","")
                    tick=self.guard.validate(PriceTick(timestamp=stamp,instrument=instrument,bid=float(bids[0]["price"]),ask=float(asks[0]["price"]),source="oanda_v20"));failures=0;yield tick
            except (OSError,ConnectionError,ValueError,json.JSONDecodeError):
                failures+=1
                if failures>=self.client.policy.retries:raise
                await asyncio.sleep(self.client.policy.base_backoff_seconds*(2**(failures-1)))
            finally:
                if response is not None:response.close()
