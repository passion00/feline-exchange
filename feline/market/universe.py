from __future__ import annotations
from dataclasses import asdict,dataclass

@dataclass(frozen=True)
class InstrumentRecord:
    instrument:str;broker_symbol:str;asset_class:str="unknown";name:str="";aliases:tuple[str,...]=();broker:str="unknown";tradable:bool=False;longable:bool=False;shortable:bool=False;market_data:bool=True
    def prompt_dict(self):return asdict(self)

class InstrumentUniverse:
    """Bounded canonical universe; model output is never allowed to create entries."""
    DEFAULTS={
      "EURUSD":("fx","Euro / US Dollar",("euro","ecb","fed","dollar")),
      "XAUUSD":("metal","Gold / US Dollar",("gold","bullion")),
      "BTCUSD":("crypto","Bitcoin / US Dollar",("bitcoin","btc")),
      "BTCUSDT":("crypto","Bitcoin / Tether",("bitcoin","btc","tether")),
    }
    def __init__(self,records=(),maximum:int=128):self.maximum=maximum;self.records={x.instrument:x for x in records}
    @classmethod
    def from_broker(cls,broker,configured:dict|None=None):
        name=getattr(broker,"adapter_name","internal_paper");caps=getattr(broker,"broker_capabilities",None);symbols=list(getattr(broker,"available_instruments",()) or getattr(broker,"quotes",{}).keys())
        symbols+=list((configured or {}).keys())
        if not symbols: symbols=list(cls.DEFAULTS)
        records=[]
        for symbol in dict.fromkeys(str(x).replace("/","").upper() for x in symbols):
            metadata=(configured or {}).get(symbol,{}) if configured else {};fallback=cls.DEFAULTS.get(symbol,(metadata.get("asset_class","unknown"),metadata.get("name",symbol),tuple(metadata.get("aliases",()))));asset_class=metadata.get("asset_class",fallback[0]);trade=bool(getattr(caps,"market_orders",False)) if caps else name=="internal_paper";shortable=bool(metadata["shortable"]) if "shortable" in metadata else bool(trade and asset_class in {"fx","metal","crypto"});records.append(InstrumentRecord(symbol,metadata.get("broker_symbol",symbol),asset_class,metadata.get("name",fallback[1]),tuple(metadata.get("aliases",fallback[2])),name,trade,trade,shortable,True))
        return cls(records)
    def get(self,instrument):return self.records.get(str(instrument).replace("/","").upper())
    def bounded_prompt(self):return [self.records[key].prompt_dict() for key in sorted(self.records)[:self.maximum]]
    def __contains__(self,instrument):return self.get(instrument) is not None
