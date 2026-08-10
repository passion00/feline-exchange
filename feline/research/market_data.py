"""Provider-independent, non-repairing OHLC dataset quality inspection."""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from feline.market.profiles import MarketProfile, get_market_profile
from feline.replay.session_report import file_checksum


class DatasetQualityStatus(str, Enum):
    PASS = "PASS"
    PASS_WITH_EXPECTED_CLOSURES = "PASS_WITH_EXPECTED_CLOSURES"
    REVIEW = "REVIEW"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class ContinuousDatasetQuality:
    path: str
    instrument: str
    provider: str | None
    source_symbol: str
    interval: str
    timezone: str
    requested_start: str | None
    requested_end_exclusive: str | None
    start: str | None
    end: str | None
    candle_count: int
    sha256: str
    parse_errors: tuple[str, ...]
    duplicate_timestamps: tuple[str, ...]
    non_monotonic: bool
    ohlc_errors: tuple[str, ...]
    non_finite_or_non_positive: tuple[str, ...]
    malformed_duration: tuple[str, ...]
    out_of_window: tuple[str, ...]
    expected_closures: int
    unexpected_gap_count: int
    unexpected_missing_minutes: int
    longest_unexpected_gap_minutes: int
    quality_status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("naive timestamp")
    return result.astimezone(timezone.utc)


def inspect_continuous_dataset(path: Path, instrument: str, requested_start: datetime | None = None,
                               requested_end_exclusive: datetime | None = None,
                               report_path: Path | None = None) -> ContinuousDatasetQuality:
    """Validate normalized JSONL without altering a byte of source data."""
    profile = get_market_profile(instrument); key = profile.instrument
    duplicates: list[str] = []; errors: list[str] = []; numeric: list[str] = []; durations: list[str] = []
    outside: list[str] = []; parse_errors: list[str] = []; seen: set[datetime] = set()
    previous: datetime | None = None; first = last = None; provider = None; count = expected = unexpected = missing = longest = 0
    with path.open() as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                row = json.loads(line)
                if row.get("type") not in {"candle", "ohlc"} or row.get("instrument") != key or row.get("timeframe", "1m") != "1m":
                    continue
                opened = _parse(row["open_time"]); closed = _parse(row["close_time"])
                values = tuple(float(row[name]) for name in ("open", "high", "low", "close"))
            except Exception as exc:
                parse_errors.append(f"line {line_number}: {type(exc).__name__}"); continue
            count += 1; provider = provider or row.get("source"); first = first or closed; last = closed
            stamp = closed.isoformat()
            if closed in seen: duplicates.append(stamp)
            seen.add(closed)
            if previous is not None:
                if closed <= previous: pass
                elif closed - previous > timedelta(minutes=1):
                    gap_minutes = int((closed - previous).total_seconds() // 60) - 1
                    if _gap_is_expected(previous, closed, profile): expected += 1
                    else: unexpected += 1; missing += gap_minutes; longest = max(longest, gap_minutes)
            if closed - opened != timedelta(minutes=1): durations.append(stamp)
            if requested_start and opened < requested_start.astimezone(timezone.utc): outside.append(stamp)
            if requested_end_exclusive and opened >= requested_end_exclusive.astimezone(timezone.utc): outside.append(stamp)
            if not all(math.isfinite(value) and value > 0 for value in values): numeric.append(stamp)
            else:
                o, h, lo, c = values
                if h < max(o, c, lo) or lo > min(o, c, h): errors.append(f"{stamp}: O={o} H={h} L={lo} C={c}")
            previous = closed
    non_monotonic = False
    # Duplicate and backwards ordering are separately observable; streaming
    # state identifies ordering without sorting the provider input.
    prior = None
    with path.open() as handle:
        for line in handle:
            try:
                row = json.loads(line)
                if row.get("type") in {"candle", "ohlc"} and row.get("instrument") == key:
                    current = _parse(row["close_time"])
                    if prior is not None and current < prior: non_monotonic = True
                    prior = current
            except Exception: pass
    fatal = not count or parse_errors or duplicates or non_monotonic or errors or numeric or durations or outside
    status = DatasetQualityStatus.REJECTED if fatal else DatasetQualityStatus.REVIEW if unexpected else DatasetQualityStatus.PASS_WITH_EXPECTED_CLOSURES if expected else DatasetQualityStatus.PASS
    result = ContinuousDatasetQuality(str(path.resolve()), key, provider, key, "1m", "UTC",
        requested_start.isoformat() if requested_start else None, requested_end_exclusive.isoformat() if requested_end_exclusive else None,
        first.isoformat() if first else None, last.isoformat() if last else None, count, file_checksum(path), tuple(parse_errors),
        tuple(duplicates), non_monotonic, tuple(errors), tuple(numeric), tuple(durations), tuple(outside), expected,
        unexpected, missing, longest, status.value)
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n")
    return result


def assert_dataset_research_eligible(path: Path) -> None:
    sidecar = path.with_suffix(path.suffix + ".quality.json")
    if sidecar.exists():
        status = json.loads(sidecar.read_text()).get("quality_status")
        if status == DatasetQualityStatus.REJECTED.value:
            raise ValueError(f"dataset is REJECTED and cannot enter signal research: {path}")


def _gap_is_expected(before: datetime, after: datetime, profile: MarketProfile) -> bool:
    cursor = before + timedelta(minutes=1)
    if cursor >= after: return False
    while cursor < after:
        if not profile.is_expected_closed(cursor - timedelta(minutes=1)): return False
        cursor += timedelta(minutes=1)
    return True
