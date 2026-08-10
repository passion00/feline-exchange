from __future__ import annotations

import asyncio,json,os,time,urllib.parse,urllib.request
from datetime import datetime,timezone
from uuid import uuid4

from feline.brokers.core import BrokerAdapter,BrokerCapabilities,BrokerConnectionState,BrokerProfile,UnsupportedBrokerCapability
from feline.core.events import FillEvent,OrderRequest,OrderStatus,OrderType,OrderUpdate,PriceTick,Side
from feline.market.datafeed import RetryingHTTPClient
from feline.market.oanda import OandaV20Provider
from feline.portfolio.models import Position


class OandaBrokerAdapter(OandaV20Provider,BrokerAdapter):
    adapter_name="oanda_v20"
    broker_capabilities=BrokerCapabilities(True,True,True,True,True,True,True,True,True,True,True,True,True,True,False)
    def __init__(self,profile:BrokerProfile,token:str|None=None,client:RetryingHTTPClient|None=None):
        if profile.environment=="live" and (not profile.live_execution_enabled or os.environ.get("FELINE_ENABLE_LIVE_BROKER")!="YES_I_ACCEPT_LIVE_RISK"):raise PermissionError("live execution requires profile enablement and FELINE_ENABLE_LIVE_BROKER=YES_I_ACCEPT_LIVE_RISK")
        token=token or os.environ.get(profile.credential_env)
        super().__init__(token=token,account_id=profile.account_id,environment="live" if profile.environment=="live" else "practice",client=client)
        self.profile=profile;self.state=BrokerConnectionState.DISCONNECTED;self.cash=0.;self.equity=0.;self.margin_available=0.;self.positions={};self.orders={};self.pending={};self.remote_order_ids=set();self.protective={};self.quotes={};self.fills=[];self.financing=[];self.audit_events=[];self.submitted_requests={}
    def _json(self,method,path,body=None):
        data=json.dumps(body).encode() if body is not None else None;req=self._request(self.rest_base+path);req.method=method;req.data=data
        if data:req.add_header("Content-Type","application/json")
        return self.client.json(req)
    async def connect(self):
        self.state=BrokerConnectionState.CONNECTING
        try:summary=(await asyncio.to_thread(self._json,"GET",f"/v3/accounts/{self.account_id}/summary"))["account"];self._apply_account(summary);self.state=BrokerConnectionState.CONNECTED;self._audit("connect",None,"accepted",{"account":self.account_id,"environment":self.profile.environment});return self.account_snapshot_sync()
        except Exception:self.state=BrokerConnectionState.ERROR;raise
    async def disconnect(self):self.state=BrokerConnectionState.DISCONNECTED;self._audit("disconnect",None,"accepted",{})
    async def stream(self,instruments):
        last_reconciliation=time.monotonic()
        async for tick in super().stream(instruments):
            self.update_quote(tick)
            if time.monotonic()-last_reconciliation>=60:
                try:await self.reconcile()
                except Exception as exc:self._audit("periodic_reconcile",None,"warning",{"error":type(exc).__name__})
                last_reconciliation=time.monotonic()
            yield tick
    async def account_snapshot(self):return self.account_snapshot_sync()
    def account_snapshot_sync(self):return {"balance":self.cash,"equity":self.equity,"margin_available":self.margin_available,"state":self.state.value,"account_id":self.account_id}
    async def discover_instruments(self):
        self.require("instrument_discovery");rows=(await asyncio.to_thread(self._json,"GET",f"/v3/accounts/{self.account_id}/instruments")).get("instruments",[]);return tuple(sorted(x["name"].replace("_","") for x in rows))
    async def historical_candles(self,request):self.require("historical");return await asyncio.to_thread(self.fetch,request)
    async def reconcile(self):
        account=(await asyncio.to_thread(self._json,"GET",f"/v3/accounts/{self.account_id}/summary"))["account"];remote_positions=(await asyncio.to_thread(self._json,"GET",f"/v3/accounts/{self.account_id}/openPositions")).get("positions",[]);remote_orders=(await asyncio.to_thread(self._json,"GET",f"/v3/accounts/{self.account_id}/pendingOrders")).get("orders",[]);recent_orders=(await asyncio.to_thread(self._json,"GET",f"/v3/accounts/{self.account_id}/orders?state=ALL&count=500")).get("orders",[]);before={k:(v.quantity,v.average_price) for k,v in self.positions.items()};local_pending=set(self.remote_order_ids);self._apply_account(account);self.positions={};self.remote_order_ids={str(row.get("id")) for row in remote_orders}
        for row in remote_positions:
            long=float(row["long"]["units"]);short=float(row["short"]["units"]);qty=long+short;price=float(row["long"]["averagePrice"] if qty>0 else row["short"]["averagePrice"] if qty<0 else 0);self.positions[row["instrument"].replace("_","")]=Position(row["instrument"].replace("_",""),qty,price,0)
        for row in recent_orders:
            client_id=(row.get("clientExtensions") or {}).get("id")
            if client_id:self.submitted_requests.setdefault(client_id,str(row.get("id")))
        for row in remote_orders:
            units=float(row.get("units",0));oid=str(row.get("id"));instrument=str(row.get("instrument","UNKNOWN")).replace("_","");self.orders[oid]=OrderUpdate(timestamp=self._transaction_time(row),order_id=oid,instrument=instrument,side=Side.BUY if units>=0 else Side.SELL,quantity=abs(units),status=OrderStatus.ACCEPTED,filled_quantity=0,remaining_quantity=abs(units),reason="broker reconciliation")
        disagreement=before!={k:(v.quantity,v.average_price) for k,v in self.positions.items()} or bool(local_pending and local_pending!=self.remote_order_ids);result={"positions":len(self.positions),"pending_orders":len(remote_orders),"disagreement":disagreement,"remote_order_ids":sorted(self.remote_order_ids)};self._audit("reconcile",None,"warning" if disagreement else "accepted",result);return result
    def update_quote(self,q):self.quotes[q.instrument]=q
    def get_quote(self,instrument):return self.quotes.get(instrument)
    def get_balance(self):return self.cash
    def get_positions(self):return {k:Position(**vars(v)) for k,v in self.positions.items()}
    def portfolio_state(self):
        exposure=sum(abs(p.quantity*self.quotes[k].mid) for k,p in self.positions.items() if k in self.quotes);unrealized=sum(p.unrealized(self.quotes[k].mid) for k,p in self.positions.items() if k in self.quotes);return {"cash":self.cash,"equity":self.equity or self.cash+unrealized,"realized_pnl":0.,"unrealized_pnl":unrealized,"exposure":exposure,"commission_costs":sum(x.commission for x in self.fills),"spread_costs":sum(x.spread_cost for x in self.fills),"slippage_costs":sum(x.slippage_amount for x in self.fills),"financing_costs":-sum(x.amount for x in self.financing)}
    async def process_quote(self,q):self.update_quote(q);return []
    @staticmethod
    def _transaction_time(transaction):
        value=transaction.get("time") if transaction else None
        return datetime.fromisoformat(value.replace("Z","+00:00")) if value else datetime.now(timezone.utc)
    async def submit_order(self,r:OrderRequest):
        if r.order_type is OrderType.STOP_LIMIT:raise UnsupportedBrokerCapability("oanda_v20 does not support STOP_LIMIT orders")
        if r.order_type is OrderType.LIMIT and r.limit_price is None:raise ValueError("limit_price is required for a limit order")
        if r.order_type is OrderType.STOP and r.stop_price is None:raise ValueError("stop_price is required for a stop order")
        capability={OrderType.MARKET:"market_orders",OrderType.LIMIT:"limit_orders",OrderType.STOP:"stop_orders",OrderType.STOP_LIMIT:"stop_orders"}[r.order_type];self.require(capability)
        if self.state is not BrokerConnectionState.CONNECTED:raise ConnectionError("broker is not connected")
        if r.id in self.submitted_requests:raise RuntimeError(f"duplicate broker order request blocked: {r.id}")
        units=r.quantity*(1 if r.side is Side.BUY else -1);order={"instrument":self.provider_symbol(r.instrument),"units":str(units),"type":r.order_type.value.upper().replace("_","") ,"positionFill":"DEFAULT"}
        if r.order_type is OrderType.MARKET:order["timeInForce"]="FOK"
        else:order.update({"timeInForce":"GTC","price":str(r.limit_price if r.order_type in {OrderType.LIMIT,OrderType.STOP_LIMIT} else r.stop_price)})
        order["clientExtensions"]={"id":r.id[:128],"tag":"feline"}
        if r.stop_price is not None and r.order_type is OrderType.MARKET:order["stopLossOnFill"]={"price":str(r.stop_price)}
        if r.take_profit_price is not None:order["takeProfitOnFill"]={"price":str(r.take_profit_price)}
        try:payload=await asyncio.to_thread(self._json,"POST",f"/v3/accounts/{self.account_id}/orders",{"order":order})
        except Exception as exc:self._audit("submit",r.id,"rejected",{"error":type(exc).__name__});raise
        rejected=payload.get("orderRejectTransaction");transaction=payload.get("orderFillTransaction") or payload.get("orderCreateTransaction") or rejected or {};oid=str(transaction.get("orderID") or transaction.get("id") or uuid4());self.submitted_requests[r.id]=oid;filled=payload.get("orderFillTransaction");status=OrderStatus.REJECTED if rejected else OrderStatus.FILLED if filled else OrderStatus.ACCEPTED;price=float(filled.get("price",0)) if filled else None;filled_quantity=abs(float(filled.get("units",r.quantity))) if filled else 0;update=OrderUpdate(timestamp=self._transaction_time(transaction),order_id=oid,instrument=r.instrument,side=r.side,quantity=r.quantity,status=status,fill_price=price,reason=str(rejected.get("rejectReason")) if rejected else None,filled_quantity=filled_quantity,remaining_quantity=max(0,r.quantity-filled_quantity) if filled else 0 if rejected else r.quantity,correlation_id=r.id);self.orders[oid]=update
        if filled:
            quote=self.quotes.get(r.instrument);reference=quote.mid if quote else price;executable=quote.ask if quote and r.side is Side.BUY else quote.bid if quote else price;fill=FillEvent(timestamp=update.timestamp,order_id=oid,instrument=r.instrument,side=r.side,quantity=filled_quantity,reference_price=reference,fill_price=price,gross_value=price*filled_quantity,commission=abs(float(filled.get("commission",0))),spread_cost=abs(executable-reference)*filled_quantity,slippage_amount=abs(price-executable)*filled_quantity,slippage_percentage=abs(price/executable-1) if executable else 0,latency_ms=0,correlation_id=r.id,assumptions={"external_broker":"oanda_v20","transaction_id":filled.get("id"),"broker_timestamp":filled.get("time")});self.fills.append(fill);signed_units=filled_quantity*(1 if r.side is Side.BUY else -1);self.positions.setdefault(r.instrument,Position(r.instrument)).apply_fill(signed_units,price)
            if filled.get("accountBalance") is not None:self.cash=float(filled["accountBalance"]);self.equity=self.cash
        self._audit("submit",r.id,status.value,{"broker_order_id":oid,"request":{"instrument":r.instrument,"side":r.side.value,"quantity":r.quantity,"order_type":r.order_type.value,"expected_price":r.expected_price,"limit_price":r.limit_price,"stop_price":r.stop_price,"take_profit_price":r.take_profit_price},"reject_reason":update.reason});return update
    async def cancel_order(self,order_id):self.require("cancel_orders");payload=await asyncio.to_thread(self._json,"PUT",f"/v3/accounts/{self.account_id}/orders/{order_id}/cancel");old=self.orders.get(order_id);update=OrderUpdate(order_id=order_id,instrument=old.instrument if old else "UNKNOWN",side=old.side if old else Side.BUY,quantity=old.quantity if old else 0,status=OrderStatus.CANCELLED,correlation_id=old.correlation_id if old else None);self.orders[order_id]=update;self._audit("cancel",order_id,"cancelled",{"transaction":payload.get("orderCancelTransaction",{}).get("id")});return update
    async def replace_order(self,order_id,replacement):self.require("modify_orders");await self.cancel_order(order_id);return await self.submit_order(replacement)
    async def close_position(self,instrument):
        position=self.positions.get(instrument)
        if not position or not position.quantity:return OrderUpdate(order_id=str(uuid4()),instrument=instrument,side=Side.BUY,quantity=0,status=OrderStatus.REJECTED,reason="no open position")
        return await self.submit_order(OrderRequest(instrument=instrument,side=Side.SELL if position.quantity>0 else Side.BUY,quantity=abs(position.quantity),expected_price=self.quotes[instrument].mid))
    def _apply_account(self,row):self.cash=float(row.get("balance",0));self.equity=float(row.get("NAV",self.cash));self.margin_available=float(row.get("marginAvailable",0))
    def _audit(self,kind,request_id,status,payload):self.audit_events.append({"event_id":str(uuid4()),"timestamp":datetime.now(timezone.utc).isoformat(),"kind":kind,"request_id":request_id,"status":status,"payload":payload})
    def drain_audit_events(self):rows=list(self.audit_events);self.audit_events.clear();return rows
