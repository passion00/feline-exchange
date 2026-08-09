from dataclasses import dataclass

@dataclass(frozen=True)
class InstrumentProfile:
 symbol:str;asset_class:str;price_precision:int;quantity_precision:int;minimum_quantity:float;contract_size:float;synthetic_spread_bps:float;calendar:str;timezone:str;maximum_paper_leverage:float

PROFILES={
 "EURUSD":InstrumentProfile("EURUSD","fx",5,2,.01,100000,2,"fx_24_5","UTC",30),
 "GBPUSD":InstrumentProfile("GBPUSD","fx",5,2,.01,100000,2.5,"fx_24_5","UTC",30),
 "BIST_DEMO":InstrumentProfile("BIST_DEMO","equity",2,0,1,1,10,"exchange","Europe/Istanbul",1),
 "US_DEMO":InstrumentProfile("US_DEMO","equity",2,0,1,1,5,"exchange","America/New_York",1),
}
