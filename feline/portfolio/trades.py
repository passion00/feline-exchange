from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from uuid import uuid4

class ExitReason(str,Enum):STRATEGY="strategy_exit";STOP="stop_loss";TARGET="take_profit";RISK="risk_liquidation";KILL="kill_switch";FORCE_CLOSE="replay_force_close";MANUAL="manual_paper_close"
@dataclass
class Trade:
 trade_id:str;instrument:str;direction:str;strategy:str;strategy_version:str;signal_id:str|None;entry_time:datetime;quantity:float;average_entry:float;exit_time:datetime|None=None;average_exit:float|None=None;gross_pnl:float=0;net_pnl:float=0;commissions:float=0;spread_cost:float=0;slippage_cost:float=0;financing_cost:float=0;mae:float=0;mfe:float=0;exit_reason:ExitReason|None=None;regime_entry:str="unknown";regime_exit:str="unknown";experiment_id:str|None=None;realtime_session_id:str|None=None
 @property
 def holding_seconds(self):return (self.exit_time-self.entry_time).total_seconds() if self.exit_time else None
 def update_excursion(self,bid:float,ask:float):
  price=bid if self.direction=="long" else ask;move=(price-self.average_entry)*(1 if self.direction=="long" else -1);self.mfe=max(self.mfe,move);self.mae=max(self.mae,-move)
 def close(self,time:datetime,price:float,reason:ExitReason,costs:float=0,spread_cost:float=0,slippage_cost:float=0,financing_cost:float=0):
  self.exit_time=time;self.average_exit=price;self.exit_reason=reason;self.gross_pnl=(price-self.average_entry)*self.quantity*(1 if self.direction=="long" else -1);self.commissions+=costs;self.spread_cost+=spread_cost;self.slippage_cost+=slippage_cost;self.financing_cost+=financing_cost;self.net_pnl=self.gross_pnl-self.commissions-self.financing_cost

class TradeLifecycle:
 def __init__(self):self.open:dict[str,Trade]={};self.completed:list[Trade]=[]
 def start(self,instrument,direction,strategy,version,signal_id,time,quantity,price,realtime_session_id=None):
  trade=Trade(str(uuid4()),instrument,direction,strategy,version,signal_id,time,quantity,price,realtime_session_id=realtime_session_id);self.open[instrument]=trade;return trade
 def update(self,instrument,bid,ask):
  if instrument in self.open:self.open[instrument].update_excursion(bid,ask)
 def close(self,instrument,time,price,reason,costs=0,spread_cost=0,slippage_cost=0,financing_cost=0):
  trade=self.open.pop(instrument);trade.close(time,price,reason,costs,spread_cost,slippage_cost,financing_cost);self.completed.append(trade);return trade
