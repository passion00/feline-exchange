from __future__ import annotations
import random

class ExcursionTracker:
    def __init__(self,entry:float,long:bool=True)->None:self.entry=entry;self.long=long;self.mae=0.;self.mfe=0.
    def update(self,high:float,low:float)->None:
        favorable=(high-self.entry if self.long else self.entry-low);adverse=(self.entry-low if self.long else high-self.entry)
        self.mfe=max(self.mfe,favorable);self.mae=max(self.mae,adverse)

def stress_resample(trades:list[float],iterations:int=1000,seed:int=0,ruin_fraction:float=.5)->dict:
    rng=random.Random(seed);drawdowns=[];streaks=[];ruins=0
    if not trades:return {"iterations":iterations,"approximate_ruin_probability":0,"median_max_drawdown":0,"worst_losing_streak":0}
    for _ in range(iterations):
        sample=[rng.choice(trades) for _ in trades];equity=peak=1.;maxdd=0;streak=worst=0
        for value in sample:
            equity+=value;peak=max(peak,equity);maxdd=max(maxdd,(peak-equity)/peak if peak else 1);streak=streak+1 if value<0 else 0;worst=max(worst,streak)
        ruins+=equity<=ruin_fraction;drawdowns.append(maxdd);streaks.append(worst)
    return {"iterations":iterations,"approximate_ruin_probability":ruins/iterations,"median_max_drawdown":sorted(drawdowns)[iterations//2],"worst_losing_streak":max(streaks)}
