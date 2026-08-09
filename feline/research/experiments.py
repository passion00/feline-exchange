from __future__ import annotations
from dataclasses import asdict,dataclass
from datetime import datetime,timezone
import itertools,json,subprocess
import hashlib
from uuid import uuid4

@dataclass(frozen=True)
class Experiment:
    experiment_id:str;strategy:str;strategy_version:str;configuration:dict;instruments:tuple[str,...];dataset:str;date_range:tuple[str|None,str|None];seed:int;initial_equity:float;execution:dict;risk:dict;created_at:str;git_version:str|None=None;dataset_checksum:str|None=None;repository_dirty:bool=False;feline_version:str="0.4.0";objective:str="net_return"

def dataset_checksum(path:str)->str:
    digest=hashlib.sha256()
    with open(path,"rb") as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b""):digest.update(chunk)
    return digest.hexdigest()

def create_experiment(strategy:str,dataset:str,configuration:dict,seed:int=0,instruments:tuple[str,...]=())->Experiment:
    try:version=subprocess.run(["git","rev-parse","--short","HEAD"],capture_output=True,text=True,timeout=1).stdout.strip() or None
    except Exception:version=None
    dirty=bool(subprocess.run(["git","status","--porcelain"],capture_output=True,text=True).stdout.strip())
    return Experiment(str(uuid4()),strategy,"0.4.0",configuration,instruments,dataset,(None,None),seed,100000,{}, {},datetime.now(timezone.utc).isoformat(),version,dataset_checksum(dataset),dirty)

def parameter_grid(grid:dict,maximum:int=64):
    keys=sorted(grid);values=[grid[k] if isinstance(grid[k],list) else [grid[k]] for k in keys]
    for index,combo in enumerate(itertools.product(*values)):
        if index>=maximum:break
        yield dict(zip(keys,combo))

def walk_forward_windows(timestamps:list,train_size:int,test_size:int,step:int|None=None):
    step=step or test_size;windows=[];start=0
    while start+train_size+test_size<=len(timestamps):windows.append((timestamps[start],timestamps[start+train_size-1],timestamps[start+train_size],timestamps[start+train_size+test_size-1]));start+=step
    return windows
