"""Deterministic continuous OHLC quality inspection."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from feline.core.events import CandleUpdate
from feline.market.profiles import MarketProfile, get_market_profile
from feline.replay.mixed import read_mixed_events
from feline.replay.session_report import file_checksum


@dataclass(frozen=True)
class ContinuousDatasetQuality:
    path: str
    instrument: str
    provider: str | None
    source_symbol: str
    interval: str
    timezone: str
    start: str | None
    end: str | None
    candle_count: int
    sha256: str
    duplicate_timestamps: tuple[str, ...]
    non_monotonic: bool
    ohlc_errors: tuple[str, ...]
    expected_closures: int
    unexpected_gap_count: int
    quality_status: str

    def to_dict(self) -> dict[str, Any]: return asdict(self)


def inspect_continuous_dataset(path: Path, instrument: str) -> ContinuousDatasetQuality:
    profile=get_market_profile(instrument);rows=[event for event in read_mixed_events(path) if isinstance(event,CandleUpdate) and event.instrument==profile.instrument and event.timeframe=="1m"]
    duplicates=[];errors=[];expected=unexpected=0;seen=set();previous=None;non_monotonic=False
    for row in rows:
        if row.close_time in seen:duplicates.append(row.close_time.isoformat())
        seen.add(row.close_time)
        if min(row.open,row.high,row.low,row.close)<=0 or row.high<max(row.open,row.close,row.low) or row.low>min(row.open,row.close,row.high):errors.append(row.close_time.isoformat())
        if previous is not None:
            if row.close_time<=previous:non_monotonic=True
            elif row.close_time-previous>timedelta(minutes=1):
                if _gap_is_expected(previous,row.close_time,profile):expected+=1
                else:unexpected+=1
        previous=row.close_time
    status="clean" if rows and not duplicates and not errors and not non_monotonic and not unexpected else "review_required"
    return ContinuousDatasetQuality(str(path.resolve()),profile.instrument,rows[0].source if rows else None,profile.instrument,"1m","UTC",rows[0].close_time.isoformat() if rows else None,rows[-1].close_time.isoformat() if rows else None,len(rows),file_checksum(path),tuple(duplicates),non_monotonic,tuple(errors),expected,unexpected,status)


def _gap_is_expected(before, after, profile: MarketProfile) -> bool:
    cursor=before+timedelta(minutes=1)
    if cursor>=after:return False
    while cursor<after:
        # Dataset timestamps are candle-close times; closure applies to the
        # candle's open interval, not its visibility timestamp.
        if not profile.is_expected_closed(cursor-timedelta(minutes=1)):return False
        cursor+=timedelta(minutes=1)
    return True
