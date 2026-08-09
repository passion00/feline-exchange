from __future__ import annotations

from dataclasses import asdict, dataclass
import math


@dataclass(frozen=True)
class BacktestReport:
    starting_equity: float; ending_equity: float; total_return: float; realized_pnl: float
    number_of_trades: int; winning_trades: int; losing_trades: int; win_rate: float
    average_win: float; average_loss: float; profit_factor: float | None; maximum_drawdown: float
    average_trade: float; exposure_time_ratio: float; sharpe_like: float | None; return_volatility: float

    def to_dict(self): return asdict(self)


def calculate_report(starting: float, equities: list[float], trade_pnls: list[float], exposure_samples: int, total_samples: int) -> BacktestReport:
    ending=equities[-1] if equities else starting; wins=[x for x in trade_pnls if x>0]; losses=[x for x in trade_pnls if x<0]
    returns=[equities[i]/equities[i-1]-1 for i in range(1,len(equities)) if equities[i-1]]
    mean=sum(returns)/len(returns) if returns else 0; vol=math.sqrt(sum((x-mean)**2 for x in returns)/(len(returns)-1)) if len(returns)>1 else 0
    peak=starting; maxdd=0
    for value in equities: peak=max(peak,value); maxdd=max(maxdd,(peak-value)/peak if peak else 0)
    gross_win=sum(wins); gross_loss=-sum(losses)
    return BacktestReport(starting,ending,ending/starting-1 if starting else 0,sum(trade_pnls),len(trade_pnls),len(wins),len(losses),len(wins)/len(trade_pnls) if trade_pnls else 0,sum(wins)/len(wins) if wins else 0,sum(losses)/len(losses) if losses else 0,gross_win/gross_loss if gross_loss else None,maxdd,sum(trade_pnls)/len(trade_pnls) if trade_pnls else 0,exposure_samples/total_samples if total_samples else 0,mean/vol*math.sqrt(len(returns)) if vol else None,vol)
