from __future__ import annotations
import asyncio
from datetime import datetime,timezone
from uuid import uuid4
from feline.brokers.core import BrokerAdapter,BrokerCapabilities,BrokerConnectionState,BrokerProfile
from feline.core.events import FillEvent,OrderRequest,OrderStatus,OrderUpdate,PriceTick,Side
from feline.portfolio.models import Position

class MockBrokerAdapter(BrokerAdapter):
 adapter_name="mock";broker_capabilities=BrokerCapabilities(authentication=False,practice=True,quotes=True,historical=False,instrument_discovery=True,account=True,positions=True,orders=True,market_orders=True,limit_orders=True,stop_orders=True,modify_orders=True,cancel_orders=True,execution_updates=True)
 def __init__(self,profile:BrokerProfile|None=None,reject=False,partial=False):self.profile=profile or BrokerProfile(adapter="mock",environment="practice",credential_env="");self.reject=reject;self.partial=partial;self.state=BrokerConnectionState.DISCONNECTED;self.cash=100000.;self.positions={};self.orders={};self.pending={};self.protective={};self.quotes={};self.fills=[];self.financing=[];self.audit_events=[];self.running=False;self.submitted_requests={}
 async def connect(self):self.state=BrokerConnectionState.CONNECTED;self.running=True;return await self.account_snapshot()
 async def disconnect(self):self.state=BrokerConnectionState.DISCONNECTED;self.running=False
 async def stream(self,instruments):
  sequence=0
  while self.running:
   sequence+=1;mid=1.1+sequence*.00001;tick=PriceTick(timestamp=datetime.now(timezone.utc),instrument=instruments[0],bid=mid-.00005,ask=mid+.00005,source="mock_broker");self.quotes[tick.instrument]=tick;yield tick;await asyncio.sleep(.001)
 async def account_snapshot(self):return {"balance":self.cash,"equity":self.portfolio_state()["equity"],"margin_available":self.cash,"state":self.state.value,"account_id":self.profile.account_id}
 async def discover_instruments(self):return ("EURUSD","XAUUSD")
 async def reconcile(self):return {"positions":len(self.positions),"pending_orders":len(self.pending),"disagreement":False}
 def update_quote(self,q):self.quotes[q.instrument]=q
 async def process_quote(self,q):self.update_quote(q);return []
 def get_quote(self,i):return self.quotes.get(i)
 def get_balance(self):return self.cash
 def get_positions(self):return {k:Position(**vars(v)) for k,v in self.positions.items()}
 def portfolio_state(self):
  unreal=sum(p.unrealized(self.quotes[k].mid) for k,p in self.positions.items() if k in self.quotes);return {"cash":self.cash,"equity":self.cash+sum(p.market_value(self.quotes[k].mid) for k,p in self.positions.items() if k in self.quotes),"realized_pnl":sum(p.realized_pnl for p in self.positions.values()),"unrealized_pnl":unreal,"exposure":sum(abs(p.quantity*self.quotes[k].mid) for k,p in self.positions.items() if k in self.quotes),"commission_costs":0.,"spread_costs":0.,"slippage_costs":0.,"financing_costs":0.}
 async def submit_order(self,r:OrderRequest):
  if self.state is not BrokerConnectionState.CONNECTED:raise ConnectionError("mock disconnected")
  if r.id in self.submitted_requests:raise RuntimeError(f"duplicate broker order request blocked: {r.id}")
  oid=str(uuid4())
  if self.reject:update=OrderUpdate(order_id=oid,instrument=r.instrument,side=r.side,quantity=r.quantity,status=OrderStatus.REJECTED,reason="mock reject",correlation_id=r.id)
  else:
   q=self.quotes[r.instrument];price=q.ask if r.side is Side.BUY else q.bid;filled_quantity=r.quantity/2 if self.partial else r.quantity;status=OrderStatus.PARTIALLY_FILLED if self.partial else OrderStatus.FILLED;update=OrderUpdate(order_id=oid,instrument=r.instrument,side=r.side,quantity=r.quantity,status=status,fill_price=price,filled_quantity=filled_quantity,remaining_quantity=r.quantity-filled_quantity,correlation_id=r.id);fill=FillEvent(order_id=oid,instrument=r.instrument,side=r.side,quantity=filled_quantity,reference_price=price,fill_price=price,gross_value=price*filled_quantity,commission=0,spread_cost=abs(price-q.mid)*filled_quantity,slippage_amount=0,slippage_percentage=0,latency_ms=0,correlation_id=r.id);self.fills.append(fill);signed=filled_quantity*(1 if r.side is Side.BUY else -1);self.positions.setdefault(r.instrument,Position(r.instrument)).apply_fill(signed,price);self.cash-=signed*price
  self.orders[oid]=update;self.submitted_requests[r.id]=oid;self.audit_events.append({"event_id":str(uuid4()),"timestamp":datetime.now(timezone.utc).isoformat(),"kind":"submit","request_id":r.id,"status":update.status.value,"payload":{"broker_order_id":oid,"request":{"instrument":r.instrument,"side":r.side.value,"quantity":r.quantity,"order_type":r.order_type.value,"expected_price":r.expected_price,"limit_price":r.limit_price,"stop_price":r.stop_price,"take_profit_price":r.take_profit_price}}});return update
 async def cancel_order(self,oid):
  self.require("cancel_orders");old=self.orders[oid];update=OrderUpdate(order_id=oid,instrument=old.instrument,side=old.side,quantity=old.quantity,status=OrderStatus.CANCELLED,correlation_id=old.correlation_id);self.orders[oid]=update;return update
 async def replace_order(self,oid,r):await self.cancel_order(oid);return await self.submit_order(r)
 async def close_position(self,i):
  p=self.positions.get(i)
  if not p or not p.quantity:return OrderUpdate(order_id=str(uuid4()),instrument=i,side=Side.BUY,quantity=0,status=OrderStatus.REJECTED)
  return await self.submit_order(OrderRequest(instrument=i,side=Side.SELL if p.quantity>0 else Side.BUY,quantity=abs(p.quantity),expected_price=self.quotes[i].mid))
 def drain_audit_events(self):rows=list(self.audit_events);self.audit_events.clear();return rows
