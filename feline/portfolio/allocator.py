from __future__ import annotations

from dataclasses import dataclass
from feline.core.events import SignalEvent

@dataclass(frozen=True)
class AllocationConfig:
    max_instrument_fraction:float=.25
    max_strategy_fraction:float=.5
    correlation_group_fraction:float=.4

class PortfolioAllocator:
    """Deterministic sizing proposal; RiskEngine remains the final veto."""
    def __init__(self,config:AllocationConfig|None=None,groups:dict[str,str]|None=None)->None:self.config=config or AllocationConfig();self.groups=groups or {}
    def allocate(self,signal:SignalEvent,equity:float,cash:float,current_exposure:float,group_exposure:dict[str,float]|None=None)->float:
        budget=min(equity*self.config.max_instrument_fraction,equity*self.config.max_strategy_fraction,max(0,cash),max(0,equity-current_exposure))
        group=self.groups.get(signal.instrument);used=(group_exposure or {}).get(group,0) if group else 0
        if group:budget=min(budget,max(0,equity*self.config.correlation_group_fraction-used))
        return max(0,budget/signal.price if signal.price>0 else 0)
