"""Provider-native historical market-data download and normalization.

Dukascopy BI5 ticks are aggregated on their native BID side. Binance Spot
daily klines retain the exchange's BTCUSDT symbol and provider OHLCV.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import lzma
import os
import struct
import time
import urllib.error
import urllib.request
import zipfile
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from feline.market.profiles import get_market_profile
from feline.market.datafeed import HistoricalDataProvider, HistoricalRequest, ProviderCapabilities

UTC = timezone.utc
NATIVE_DATA_VERSION = "1.0"
DUKASCOPY_URL = "https://datafeed.dukascopy.com/datafeed"
BINANCE_URL = "https://data.binance.vision/data/spot/daily/klines"
_DUKASCOPY = {"EURUSD": ("EURUSD", 100_000), "XAUUSD": ("XAUUSD", 1_000)}


def _utc(value: str | datetime) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if result.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return result.astimezone(UTC)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _download(url: str, destination: Path, retries: int = 3) -> bool:
    """Download atomically. A valid cache is immutable unless explicitly removed."""
    if destination.exists() and destination.stat().st_size:
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "FelineExchange native-research/1.0"})
            with urllib.request.urlopen(request, timeout=45) as response, temporary.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            os.replace(temporary, destination)
            return True
        except urllib.error.HTTPError as exc:
            temporary.unlink(missing_ok=True)
            if exc.code == 404:
                return False
            if attempt + 1 == retries:
                raise
        except (OSError, urllib.error.URLError):
            temporary.unlink(missing_ok=True)
            if attempt + 1 == retries:
                raise
        time.sleep(1.5 * (attempt + 1))
    return False


@dataclass(frozen=True)
class NativeDatasetResult:
    output: str
    provenance: str
    rows: int
    processed_sha256: str
    source_files: int
    reused_files: int
    downloaded_files: int


def parse_dukascopy_bi5(data: bytes, hour: datetime, instrument: str, price_basis: str = "BID") -> list[tuple[datetime, float, float]]:
    """Decode official 20-byte BI5 tick records as (timestamp, price, volume)."""
    key = instrument.replace("/", "").upper()
    if key not in _DUKASCOPY:
        raise ValueError("Dukascopy native support is EURUSD and XAUUSD")
    if price_basis not in {"BID", "ASK"}:
        raise ValueError("Dukascopy price basis must be BID or ASK")
    raw = lzma.decompress(data)
    if len(raw) % 20:
        raise ValueError("malformed Dukascopy BI5 record length")
    divisor = _DUKASCOPY[key][1]
    ticks = []
    for offset in range(0, len(raw), 20):
        milliseconds, ask, bid, ask_volume, bid_volume = struct.unpack(">IIIff", raw[offset:offset + 20])
        if milliseconds >= 3_600_000:
            raise ValueError("Dukascopy tick offset is outside source hour")
        price = (bid if price_basis == "BID" else ask) / divisor
        volume = bid_volume if price_basis == "BID" else ask_volume
        ticks.append((hour + timedelta(milliseconds=milliseconds), price, float(volume)))
    return ticks


def aggregate_ticks(ticks: Iterable[tuple[datetime, float, float]], instrument: str, source: str = "dukascopy") -> list[dict]:
    candles: dict[datetime, dict] = {}
    for timestamp, price, volume in ticks:
        minute = timestamp.astimezone(UTC).replace(second=0, microsecond=0)
        row = candles.get(minute)
        if row is None:
            row = candles[minute] = {"open": price, "high": price, "low": price, "close": price, "volume": volume, "ticks": 1}
        else:
            row["high"] = max(row["high"], price); row["low"] = min(row["low"], price)
            row["close"] = price; row["volume"] += volume; row["ticks"] += 1
    return [_normalized(minute, instrument, row, source, "native", {"price_basis": "BID", "source_resolution": "tick", "tick_count": row["ticks"]})
            for minute, row in sorted(candles.items())]


def _normalized(open_time: datetime, instrument: str, row: dict, source: str, provenance: str, metadata: dict | None = None) -> dict:
    close_time = open_time + timedelta(minutes=1)
    result = {"type": "candle", "timestamp": close_time.isoformat(), "instrument": instrument,
              "timeframe": "1m", "open_time": open_time.isoformat(), "close_time": close_time.isoformat(),
              "open": float(row["open"]), "high": float(row["high"]), "low": float(row["low"]),
              "close": float(row["close"]), "volume": float(row.get("volume", 0) or 0),
              "source": source, "provenance": provenance}
    if metadata:
        result["provider_metadata"] = metadata
    return result


def download_dukascopy(instrument: str, start: str | datetime, end: str | datetime, output: Path,
                       raw_root: Path = Path("data/historical/raw/dukascopy")) -> NativeDatasetResult:
    key = instrument.replace("/", "").upper(); source_symbol, _ = _DUKASCOPY[key]
    begin, finish = _utc(start), _utc(end)
    if begin >= finish:
        raise ValueError("start must precede exclusive end")
    rows, source_files, reused, downloaded = [], [], 0, 0
    profile = get_market_profile(key)
    hours=[];hour = begin.replace(minute=0, second=0, microsecond=0)
    while hour < finish:
        if not profile.is_expected_closed(hour):hours.append(hour)
        hour += timedelta(hours=1)
    def fetch(hour):
        relative = Path(source_symbol) / f"{hour.year}" / f"{hour.month - 1:02d}" / f"{hour.day:02d}" / f"{hour.hour:02d}h_ticks.bi5"
        target = raw_root / relative
        existed = target.exists()
        fetched = _download(f"{DUKASCOPY_URL}/{relative.as_posix()}", target)
        return hour,target,existed,fetched
    # BI5 is split hourly; bounded concurrency keeps six-month acquisition
    # practical while each request remains independently retryable/cached.
    with ThreadPoolExecutor(max_workers=24) as pool:
      fetched_hours=list(pool.map(fetch,hours))
    for hour,target,existed,fetched in sorted(fetched_hours):
        downloaded += int(fetched); reused += int(existed and not fetched)
        if target.exists() and target.stat().st_size:
            source_files.append({"path": str(target), "sha256": _sha(target)})
            for row in aggregate_ticks(parse_dukascopy_bi5(target.read_bytes(), hour, key), key):
                opened = _utc(row["open_time"])
                if begin <= opened < finish:
                    rows.append(row)
    return _write_native(rows, output, {"provider": "dukascopy", "requested_symbol": source_symbol,
        "instrument": key, "price_basis": "BID", "source_resolution": "tick", "output_resolution": "1m",
        "requested_start": begin.isoformat(), "requested_end_exclusive": finish.isoformat(), "source_files": source_files}, reused, downloaded)


def parse_binance_kline_csv(content: bytes, instrument: str = "BTCUSDT") -> list[dict]:
    if instrument.replace("/", "").upper() != "BTCUSDT":
        raise ValueError("Binance native archive support requires BTCUSDT")
    rows = []
    for fields in csv.reader(io.StringIO(content.decode("utf-8"))):
        if not fields or not fields[0].isdigit():
            continue
        if len(fields) < 12:
            raise ValueError("malformed Binance kline row")
        opened = datetime.fromtimestamp(int(fields[0]) / 1000, UTC)
        row = {"open": fields[1], "high": fields[2], "low": fields[3], "close": fields[4], "volume": fields[5]}
        rows.append(_normalized(opened, "BTCUSDT", row, "binance_spot", "native", {
            "provider_symbol": "BTCUSDT", "venue": "Binance Spot", "provider_close_timestamp_ms": int(fields[6]),
            "quote_volume": float(fields[7]), "trade_count": int(fields[8]),
            "taker_buy_base_volume": float(fields[9]), "taker_buy_quote_volume": float(fields[10])}))
    return rows


def verify_binance_checksum(zip_path: Path, checksum_path: Path) -> str:
    expected = checksum_path.read_text().strip().split()[0].lower()
    actual = _sha(zip_path)
    if len(expected) != 64 or expected != actual:
        raise ValueError(f"Binance checksum mismatch for {zip_path.name}")
    return actual


def download_binance(start: str | datetime, end: str | datetime, output: Path,
                     raw_root: Path = Path("data/historical/raw/binance")) -> NativeDatasetResult:
    begin, finish = _utc(start), _utc(end)
    if begin >= finish:
        raise ValueError("start must precede exclusive end")
    rows, source_files, reused, downloaded = [], [], 0, 0
    day = begin.replace(hour=0, minute=0, second=0, microsecond=0)
    while day < finish:
        name = f"BTCUSDT-1m-{day:%Y-%m-%d}.zip"; target = raw_root / "spot" / "BTCUSDT" / "1m" / name
        checksum = target.with_suffix(target.suffix + ".CHECKSUM")
        base = f"{BINANCE_URL}/BTCUSDT/1m/{name}"
        existed = target.exists() and checksum.exists()
        downloaded += int(_download(base, target)); downloaded += int(_download(base + ".CHECKSUM", checksum))
        reused += int(existed)
        if not target.exists() or not checksum.exists():
            raise FileNotFoundError(f"required Binance daily archive unavailable: {name}")
        digest = verify_binance_checksum(target, checksum)
        source_files.append({"path": str(target), "sha256": digest, "checksum_file": str(checksum), "checksum_verified": True})
        with zipfile.ZipFile(target) as archive:
            members = [item for item in archive.namelist() if item.endswith(".csv")]
            if len(members) != 1:
                raise ValueError(f"unexpected Binance archive contents: {name}")
            for row in parse_binance_kline_csv(archive.read(members[0])):
                opened = _utc(row["open_time"])
                if begin <= opened < finish:
                    rows.append(row)
        day += timedelta(days=1)
    return _write_native(rows, output, {"provider": "binance", "venue": "Binance Spot", "requested_symbol": "BTCUSDT",
        "instrument": "BTCUSDT", "price_basis": "spot_trades", "source_resolution": "1m_kline", "output_resolution": "1m",
        "requested_start": begin.isoformat(), "requested_end_exclusive": finish.isoformat(), "source_files": source_files}, reused, downloaded)


def _write_native(rows: list[dict], output: Path, provenance: dict, reused: int, downloaded: int) -> NativeDatasetResult:
    rows.sort(key=lambda row: row["open_time"])
    if len({row["open_time"] for row in rows}) != len(rows):
        raise ValueError("duplicate provider bars during deterministic merge")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".part")
    with temporary.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    if output.exists() and _sha(output) != _sha(temporary):
        temporary.unlink()
        raise FileExistsError(f"refusing to overwrite non-identical processed dataset: {output}")
    if output.exists(): temporary.unlink()
    else: os.replace(temporary, output)
    raw_digest=hashlib.sha256("".join(item["sha256"] for item in provenance["source_files"]).encode()).hexdigest()
    try:commit=subprocess.run(["git","rev-parse","HEAD"],capture_output=True,text=True,check=True,timeout=2).stdout.strip()
    except Exception:commit="unknown"
    from feline import __version__
    provenance.update({"dataset_id":hashlib.sha256((provenance["provider"]+provenance["instrument"]+provenance["requested_start"]+provenance["requested_end_exclusive"]+_sha(output)).encode()).hexdigest()[:24],
                       "downloader_importer_version": NATIVE_DATA_VERSION, "feline_version":__version__,"git_commit":commit,
                       "imported_at_utc":datetime.now(UTC).isoformat(),"raw_file_set_sha256":raw_digest,"row_count": len(rows),
                       "processed_path": str(output), "processed_sha256": _sha(output)})
    provenance_path = output.with_suffix(output.suffix + ".provenance.json")
    provenance_path.write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n")
    return NativeDatasetResult(str(output), str(provenance_path), len(rows), _sha(output), len(provenance["source_files"]), reused, downloaded)


class DukascopyHistoricalProvider(HistoricalDataProvider):
    capabilities=ProviderCapabilities("dukascopy",True,False,True,False,("EURUSD","XAUUSD"))
    def __init__(self,raw_root:Path=Path("data/historical/raw/dukascopy")):self.raw_root=raw_root
    def acquire(self,request:HistoricalRequest,output:Path):
        if request.price_basis.lower()!="bid":raise ValueError("Dukascopy normalized historical provider uses native BID ticks")
        return download_dukascopy(request.instrument,request.start,request.end_exclusive,output,self.raw_root)


class BinanceSpotHistoricalProvider(HistoricalDataProvider):
    capabilities=ProviderCapabilities("binance_spot",True,False,True,False,("BTCUSDT",))
    def __init__(self,raw_root:Path=Path("data/historical/raw/binance")):self.raw_root=raw_root
    def acquire(self,request:HistoricalRequest,output:Path):
        if request.instrument.replace("/","").upper()!="BTCUSDT":raise ValueError("Binance Spot archive provider supports BTCUSDT")
        if request.price_basis.lower() not in {"spot","trades"}:raise ValueError("Binance Spot archive price basis must be spot")
        return download_binance(request.start,request.end_exclusive,output,self.raw_root)
