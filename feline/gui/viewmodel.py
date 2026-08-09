from __future__ import annotations
from dataclasses import dataclass,field

@dataclass
class DashboardState:
 running:bool=False;mode:str="PAPER / RESEARCH";cash:float=0;equity:float=0;realized_pnl:float=0;unrealized_pnl:float=0;drawdown:float=0;exposure:float=0;kill_switch:bool=False;ai_state:str="unknown";ai_queue:int=0;database_state:str="unknown";provider_state:str="unknown";prices:dict=field(default_factory=dict);regimes:dict=field(default_factory=dict);events:list=field(default_factory=list);positions:list=field(default_factory=list);orders:list=field(default_factory=list);fills:list=field(default_factory=list);trades:list=field(default_factory=list);macro_events:list=field(default_factory=list);analyses:list=field(default_factory=list)

class DashboardViewModel:
 """Read-only projection; contains no execution, strategy, or risk authority."""
 def __init__(self):self.state=DashboardState()
 def update_runtime(self,runtime):
  p=runtime.broker.portfolio_state();s=self.state;s.running=runtime.running;s.cash=p["cash"];s.equity=p["equity"];s.realized_pnl=p["realized_pnl"];s.unrealized_pnl=p["unrealized_pnl"];s.exposure=p["exposure"];s.kill_switch=runtime.risk.kill_switch;s.ai_queue=runtime.ai.queue.qsize();s.positions=[vars(x) for x in runtime.broker.positions.values()];s.orders=[x.payload() for x in runtime.broker.orders.values()];s.fills=[x.payload() for x in runtime.broker.fills[-20:]];s.prices={k:{"bid":v.bid,"ask":v.ask,"mid":v.mid,"spread":v.spread_ratio} for k,v in runtime.broker.quotes.items()};s.regimes={k:v.value for k,v in runtime.regimes.current.items()};return s
