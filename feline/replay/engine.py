from __future__ import annotations

import asyncio
import csv
from datetime import datetime, timezone
from pathlib import Path
import random
import heapq

from feline.core.events import PriceTick


class CSVReplayProvider:
    def __init__(self, path: Path, speed: str = "max", seed: int = 0, progress=None, reorder_window: int = 1000) -> None:
        self.path,self.speed,self.seed,self.progress = path,speed,seed,progress
        self.processed=0; self.total=0; self.paused=False; self.stopped=False;self.reorder_window=max(1,reorder_window)

    def _rows(self):
        with self.path.open(newline="",encoding="utf-8") as handle:
            yield from csv.DictReader(handle)

    async def stream(self):
        random.seed(self.seed)
        previous=None;heap=[];sequence=0
        rows=iter(self._rows()); exhausted=False
        while not exhausted or heap:
            while not exhausted and len(heap)<=self.reorder_window:
                try:row=next(rows);sequence+=1
                except StopIteration:exhausted=True;break
                timestamp=datetime.fromisoformat(row["timestamp"].replace("Z","+00:00")).astimezone(timezone.utc)
                heapq.heappush(heap,(timestamp,sequence,row))
            if not heap:break
            timestamp,_,row=heapq.heappop(heap)
            if self.stopped: break
            while self.paused: await asyncio.sleep(0.05)
            if previous and self.speed != "max":
                factor=float(self.speed); await asyncio.sleep(max(0,(timestamp-previous).total_seconds()/factor))
            previous=timestamp; self.processed+=1
            if self.progress and self.processed%1000==0: self.progress(self.processed)
            yield PriceTick(timestamp=timestamp,instrument=row["instrument"],bid=float(row["bid"]),ask=float(row["ask"]),volume=float(row.get("volume") or 0),source=row.get("source") or "csv")
