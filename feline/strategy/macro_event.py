from __future__ import annotations
from dataclasses import dataclass
from feline.macro.events import MacroOutcome,ShockState

@dataclass(frozen=True)
class MacroDecision:
 outcome:MacroOutcome;direction:str;confidence:float;reason:str

class MacroEventStrategy:
 VERSION="0.5.0"
 def evaluate(self,initial_return:float,post_return:float,shock:ShockState,spread:float,ai_direction:str|None=None)->MacroDecision:
  if shock is not ShockState.STABILIZED:return MacroDecision(MacroOutcome.NO_TRADE,"neutral",0,"unstable_market")
  if spread>.003:return MacroDecision(MacroOutcome.NO_TRADE,"neutral",0,"excessive_spread")
  if abs(initial_return)<.001 or abs(post_return)<.0005:return MacroDecision(MacroOutcome.NO_TRADE,"neutral",.2,"insufficient_move")
  same=initial_return*post_return>0;outcome=MacroOutcome.CONTINUATION if same else MacroOutcome.MEAN_REVERSION;direction="up" if post_return>0 else "down";confidence=min(1,abs(post_return)/.005)
  return MacroDecision(outcome,direction,confidence,"quant_stabilized"+("+ai_feature" if ai_direction else ""))
