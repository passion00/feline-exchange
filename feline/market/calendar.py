from __future__ import annotations

from dataclasses import dataclass,field
from datetime import date,datetime,time,timezone
from zoneinfo import ZoneInfo


class TradingCalendar:
    def is_open(self,value:datetime)->bool:raise NotImplementedError


class FXCalendar(TradingCalendar):
    def is_open(self,value:datetime)->bool:
        value=value.astimezone(timezone.utc);weekday=value.weekday()
        return weekday<4 or weekday==4 and value.hour<22 or weekday==6 and value.hour>=22


@dataclass(frozen=True)
class ExchangeCalendar(TradingCalendar):
    timezone_name:str="UTC";open_time:time=time(9,30);close_time:time=time(16);holidays:frozenset[date]=field(default_factory=frozenset)
    def is_open(self,value:datetime)->bool:
        local=value.astimezone(ZoneInfo(self.timezone_name))
        return local.weekday()<5 and local.date() not in self.holidays and self.open_time<=local.time().replace(tzinfo=None)<self.close_time
