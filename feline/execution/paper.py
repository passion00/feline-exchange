from __future__ import annotations

from datetime import datetime,timedelta,timezone
import random
from uuid import uuid4

from feline.config import PaperConfig
from feline.core.events import FillEvent,FinancingEvent,OrderRequest,OrderStatus,OrderType,OrderUpdate,PriceTick,Side
from feline.portfolio.models import Position
from .broker import Broker
from .models import FinancingRule,PendingOrder
from .transitions import validate_transition


class PaperBroker(Broker):
    """Deterministic execution approximation, not an exchange order book."""
    def __init__(self,initial_cash:float=100_000,slippage_bps:float=1,volatility_slippage_multiplier:float=5,config:PaperConfig|None=None)->None:
        self.config=config or PaperConfig(initial_cash=initial_cash,slippage_bps=slippage_bps,volatility_slippage_multiplier=volatility_slippage_multiplier)
        self.cash=self.config.initial_cash;self.slippage_bps=self.config.slippage_bps;self.volatility_slippage_multiplier=self.config.volatility_slippage_multiplier
        self.positions:dict[str,Position]={};self.quotes:dict[str,PriceTick]={};self.orders:dict[str,OrderUpdate]={};self.pending:dict[str,PendingOrder]={};self.protective:dict[str,tuple[float|None,float|None]]={}
        self.fills:list[FillEvent]=[];self.financing:list[FinancingEvent]=[];self.financing_rules:dict[str,FinancingRule]={};self.extreme_volatility=False;self.rng=random.Random(self.config.random_seed)

    def update_quote(self,q:PriceTick)->None:self.quotes[q.instrument]=q
    def get_balance(self)->float:return self.cash
    def get_positions(self)->dict[str,Position]:return {k:Position(**vars(v)) for k,v in self.positions.items()}
    def get_quote(self,instrument:str)->PriceTick|None:return self.quotes.get(instrument)

    def _latency(self)->float:return self.config.fixed_latency_ms+self.rng.random()*self.config.variable_latency_ms
    def _update(self,p:PendingOrder,status:OrderStatus,reason:str|None=None)->OrderUpdate:
        if p.state!=status and p.state in {OrderStatus.NEW,OrderStatus.ACCEPTED,OrderStatus.PARTIALLY_FILLED}:validate_transition(p.state,status)
        p.state=status;u=OrderUpdate(order_id=p.order_id,instrument=p.request.instrument,side=p.request.side,quantity=p.request.quantity,status=status,fill_price=self.fills[-1].fill_price if self.fills and self.fills[-1].order_id==p.order_id else None,reason=reason,filled_quantity=p.filled_quantity,remaining_quantity=p.remaining_quantity,correlation_id=p.request.id);self.orders[p.order_id]=u;return u

    async def submit_order(self,r:OrderRequest)->OrderUpdate:
        oid=str(uuid4());latency=self._latency();quote=self.quotes.get(r.instrument);accepted_at=quote.timestamp if quote and latency==0 else r.timestamp;p=PendingOrder(oid,r,OrderStatus.ACCEPTED,r.quantity,accepted_at=accepted_at,eligible_at=accepted_at+timedelta(milliseconds=latency))
        if r.quantity<=0 or (r.order_type in {OrderType.LIMIT,OrderType.STOP_LIMIT} and r.limit_price is None):return self._update(p,OrderStatus.REJECTED,"invalid quantity or missing limit price")
        if r.order_type is OrderType.MARKET and quote is None:return self._update(p,OrderStatus.REJECTED,"missing quote")
        self.pending[oid]=p;accepted=self._update(p,OrderStatus.ACCEPTED)
        if quote and p.eligible_at<=quote.timestamp:
            updates=self._process_order(p,quote)
            return updates[-1] if updates else accepted
        return accepted

    def _executable(self,p:PendingOrder,q:PriceTick)->bool:
        r=p.request
        if r.order_type is OrderType.MARKET:return True
        if r.order_type is OrderType.LIMIT:return q.ask<=r.limit_price if r.side is Side.BUY else q.bid>=r.limit_price
        if r.order_type is OrderType.STOP:return q.ask>=r.stop_price if r.side is Side.BUY else q.bid<=r.stop_price
        if r.order_type is OrderType.STOP_LIMIT:
            return (q.ask>=r.stop_price and q.ask<=r.limit_price) if r.side is Side.BUY else (q.bid<=r.stop_price and q.bid>=r.limit_price)
        return False

    def _process_order(self,p:PendingOrder,q:PriceTick)->list[OrderUpdate]:
        if p.request.expires_at and q.timestamp>=p.request.expires_at:
            self.pending.pop(p.order_id,None);return [self._update(p,OrderStatus.EXPIRED)]
        if p.eligible_at and q.timestamp<p.eligible_at or not self._executable(p,q):return []
        available=p.remaining_quantity if q.volume<=0 else max(0,q.volume*self.config.liquidity_fraction)
        qty=min(p.remaining_quantity,available)
        if qty<=0:return []
        reference=q.ask if p.request.side is Side.BUY else q.bid;mid=q.mid;direction=1 if p.request.side is Side.BUY else -1
        regime=self.volatility_slippage_multiplier if self.extreme_volatility else 1
        slip_bps=(self.slippage_bps*regime+self.config.size_slippage_bps_per_unit*qty+(q.spread_ratio*10_000)*self.config.spread_slippage_factor)
        fill_price=reference*(1+direction*slip_bps/10_000);gross=qty*fill_price
        commission=max(self.config.minimum_commission,self.config.flat_commission+self.config.per_unit_commission*qty+self.config.percentage_commission*gross)
        signed=qty*direction;position=self.positions.setdefault(p.request.instrument,Position(p.request.instrument));position.apply_fill(signed,fill_price);self.cash-=signed*fill_price+commission
        latency=(q.timestamp-(p.accepted_at or q.timestamp)).total_seconds()*1000
        fill=FillEvent(order_id=p.order_id,instrument=p.request.instrument,side=p.request.side,quantity=qty,reference_price=reference,fill_price=fill_price,gross_value=gross,commission=commission,spread_cost=abs(reference-mid)*qty,slippage_amount=abs(fill_price-reference)*qty,slippage_percentage=abs(fill_price/reference-1),latency_ms=max(0,latency),timestamp=q.timestamp,correlation_id=p.request.id,assumptions={"spread":q.ask-q.bid,"liquidity":available,"extreme_volatility":self.extreme_volatility})
        self.fills.append(fill);p.filled_quantity+=qty;p.remaining_quantity-=qty
        status=OrderStatus.FILLED if p.remaining_quantity<=1e-12 else OrderStatus.PARTIALLY_FILLED
        if status is OrderStatus.FILLED:
            self.pending.pop(p.order_id,None)
            if p.request.stop_price is not None or p.request.take_profit_price is not None:self.protective[p.request.instrument]=(p.request.stop_price,p.request.take_profit_price)
        return [self._update(p,status)]

    async def process_quote(self,q:PriceTick)->list[OrderUpdate]:
        self.update_quote(q);updates=[]
        for p in list(self.pending.values()):updates.extend(self._process_order(p,q))
        position=self.positions.get(q.instrument);stop,target=self.protective.get(q.instrument,(None,None))
        if position and position.quantity>0 and ((stop is not None and q.bid<=stop) or(target is not None and q.bid>=target)):updates.append(await self.close_position(q.instrument))
        elif position and position.quantity<0 and ((stop is not None and q.ask>=stop) or(target is not None and q.ask<=target)):updates.append(await self.close_position(q.instrument))
        return updates

    async def cancel_order(self,oid:str)->OrderUpdate:
        p=self.pending.pop(oid,None)
        if p:return self._update(p,OrderStatus.CANCELLED)
        return self.orders[oid]

    async def replace_order(self,oid:str,replacement:OrderRequest)->OrderUpdate:
        await self.cancel_order(oid);return await self.submit_order(replacement)

    async def close_position(self,instrument:str)->OrderUpdate:
        p=self.positions.get(instrument)
        if not p or not p.quantity:return OrderUpdate(order_id=str(uuid4()),instrument=instrument,side=Side.SELL,quantity=0,status=OrderStatus.REJECTED,reason="no open position")
        q=self.quotes[instrument];r=OrderRequest(instrument=instrument,side=Side.SELL if p.quantity>0 else Side.BUY,quantity=abs(p.quantity),expected_price=q.mid,timestamp=q.timestamp)
        self.protective.pop(instrument,None);return await self.submit_order(r)

    def apply_financing(self,as_of:datetime,days:int=1)->list[FinancingEvent]:
        events=[]
        for instrument,p in self.positions.items():
            rule=self.financing_rules.get(instrument)
            if not rule or not p.quantity:continue
            multiplier=3 if as_of.weekday()==rule.triple_day else 1;rate=rule.long_daily_rate if p.quantity>0 else rule.short_daily_rate;amount=-abs(p.quantity*p.average_price)*rate*days*multiplier;self.cash+=amount
            e=FinancingEvent(instrument=instrument,quantity=p.quantity,days=days*multiplier,rate=rate,amount=amount,timestamp=as_of);self.financing.append(e);events.append(e)
        return events

    def portfolio_state(self)->dict:
        unrealized=sum(p.unrealized(self.quotes[s].mid) for s,p in self.positions.items() if s in self.quotes);realized=sum(p.realized_pnl for p in self.positions.values());exposure=sum(abs(p.quantity*self.quotes[s].mid) for s,p in self.positions.items() if s in self.quotes);equity=self.cash+sum(p.market_value(self.quotes[s].mid) for s,p in self.positions.items() if s in self.quotes)
        return {"cash":self.cash,"equity":equity,"realized_pnl":realized,"unrealized_pnl":unrealized,"exposure":exposure,"commission_costs":sum(f.commission for f in self.fills),"spread_costs":sum(f.spread_cost for f in self.fills),"slippage_costs":sum(f.slippage_amount for f in self.fills),"financing_costs":-sum(f.amount for f in self.financing)}

    def restore(self,cash:float,positions:dict[str,Position],pending:dict[str,PendingOrder]|None=None,protective:dict|None=None)->None:self.cash,self.positions,self.pending,self.protective=cash,positions,pending or {},protective or {}
