from __future__ import annotations
from datetime import datetime,timedelta,timezone
from feline.core.events import PriceTick

def shock_ticks(name:str,instrument:str="EURUSD",start:datetime|None=None)->list[PriceTick]:
    start=start or datetime.now(timezone.utc);ticks=[]
    for second in range(21):
        price=1.0;spread=.0002;volume=100
        if name=="fx_5pct":price=1.0 if second<10 else 1.05
        elif name=="spread_widening":spread=.0002+second*.001
        elif name=="liquidity_collapse":price=1+second*.002;volume=max(0,20-second)
        elif name=="gap_through_stop":price=1 if second<10 else .9
        elif name=="correlated":price=1+second*.003
        ticks.append(PriceTick(timestamp=start+timedelta(seconds=second),instrument=instrument,bid=price-spread/2,ask=price+spread/2,volume=volume,source="synthetic_stress"))
    return ticks
