"""Auditable market and execution profiles for paper/research simulations."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class MarketProfile:
    instrument: str
    asset_class: str
    quote_currency: str
    trading_calendar: str
    weekend_behavior: str
    base_quantity_unit: float
    contract_multiplier: float
    price_precision: int
    quantity_precision: int
    minimum_quantity: float
    maximum_quantity: float
    maximum_notional: float
    tick_size: float
    pip_size: float | None
    session_model: str
    default_execution_profile: str = "research_default"
    profile_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def is_expected_closed(self, timestamp: datetime) -> bool:
        """Project research calendar in UTC; holidays/maintenance are not inferred."""
        value = timestamp.astimezone(timezone.utc)
        if self.trading_calendar == "CRYPTO_24_7":
            return False
        # Explicit research approximation used by FX and spot-gold datasets.
        return value.weekday() == 5 or (value.weekday() == 4 and value.hour >= 22) or (value.weekday() == 6 and value.hour < 22)


@dataclass(frozen=True)
class ExecutionProfile:
    profile_name: str
    instrument: str
    spread_model: str
    spread_value: float
    spread_units: str
    base_slippage_model: str
    base_slippage_value: float
    slippage_units: str
    spread_dependent_slippage: float
    commission_model: str = "none"
    financing_model: str = "none"
    calibration_source: str = "research_default"
    calibrated: bool = False
    effective_from: str | None = None
    notes: str = "Synthetic research assumption; not a broker quote."
    profile_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


MARKET_PROFILES: dict[str, MarketProfile] = {
    "EURUSD": MarketProfile("EURUSD", "fx", "USD", "FX_24_5", "closed_weekend", 1.0, 1.0, 5, 2, 0.01, 100_000.0, 100_000.0, 0.00001, 0.0001, "FX_UTC"),
    "XAUUSD": MarketProfile("XAUUSD", "spot_metal", "USD", "METAL_WEEKDAY_RESEARCH", "closed_weekend", 1.0, 1.0, 2, 3, 0.001, 100.0, 100_000.0, 0.01, None, "FX_UTC"),
    "BTCUSD": MarketProfile("BTCUSD", "crypto", "USD", "CRYPTO_24_7", "continuous", 1.0, 1.0, 2, 6, 0.000001, 2.0, 100_000.0, 0.01, None, "UTC_LIQUIDITY_SEGMENTS"),
    "BTCUSDT": MarketProfile("BTCUSDT", "crypto_spot", "USDT", "CRYPTO_24_7", "continuous", 1.0, 1.0, 2, 6, 0.000001, 2.0, 100_000.0, 0.01, None, "UTC_LIQUIDITY_SEGMENTS"),
    # Retained compatibility profiles alongside the native v0.11.4 set.
    "GBPUSD": MarketProfile("GBPUSD", "fx", "USD", "FX_24_5", "closed_weekend", 1.0, 1.0, 5, 2, 0.01, 100_000.0, 100_000.0, 0.00001, 0.0001, "FX_UTC"),
    "BIST_DEMO": MarketProfile("BIST_DEMO", "equity", "TRY", "EXCHANGE", "closed_weekend", 1.0, 1.0, 2, 0, 1.0, 100_000.0, 100_000.0, 0.01, None, "EXCHANGE_LOCAL"),
    "US_DEMO": MarketProfile("US_DEMO", "equity", "USD", "EXCHANGE", "closed_weekend", 1.0, 1.0, 2, 0, 1.0, 100_000.0, 100_000.0, 0.01, None, "EXCHANGE_LOCAL"),
}

# EURUSD exactly preserves v0.11.1. Gold/crypto values are deliberately
# conservative, configurable research defaults and are not venue calibration.
_RESEARCH_EXECUTION: dict[str, ExecutionProfile] = {
    "EURUSD": ExecutionProfile("research_default", "EURUSD", "synthetic_full_spread", 2.0, "bps", "fixed_adverse_per_fill", 1.0, "bps", 0.05),
    "XAUUSD": ExecutionProfile("research_default", "XAUUSD", "synthetic_full_spread", 3.0, "bps", "fixed_adverse_per_fill", 1.5, "bps", 0.05),
    "BTCUSD": ExecutionProfile("research_default", "BTCUSD", "synthetic_full_spread", 5.0, "bps", "fixed_adverse_per_fill", 2.0, "bps", 0.05),
    "BTCUSDT": ExecutionProfile("research_default", "BTCUSDT", "synthetic_full_spread", 5.0, "bps", "fixed_adverse_per_fill", 2.0, "bps", 0.05,
                                calibration_source="research_default", calibrated=False,
                                notes="Uncalibrated continuity assumption copied from BTCUSD; not a Binance fee model."),
    "GBPUSD": ExecutionProfile("research_default", "GBPUSD", "synthetic_full_spread", 2.5, "bps", "fixed_adverse_per_fill", 1.0, "bps", 0.05),
    "BIST_DEMO": ExecutionProfile("research_default", "BIST_DEMO", "synthetic_full_spread", 10.0, "bps", "fixed_adverse_per_fill", 1.0, "bps", 0.05),
    "US_DEMO": ExecutionProfile("research_default", "US_DEMO", "synthetic_full_spread", 5.0, "bps", "fixed_adverse_per_fill", 1.0, "bps", 0.05),
}


def get_market_profile(instrument: str) -> MarketProfile:
    key = instrument.replace("/", "").upper()
    try:
        return MARKET_PROFILES[key]
    except KeyError as exc:
        raise ValueError(f"unsupported research market: {instrument}") from exc


def get_execution_profile(instrument: str, name: str = "research_default") -> ExecutionProfile:
    key = instrument.replace("/", "").upper()
    get_market_profile(key)
    if name == "reference_zero_cost":
        return ExecutionProfile(name, key, "zero", 0.0, "bps", "zero", 0.0, "bps", 0.0,
                                calibration_source="frictionless_reference", calibrated=False,
                                notes="Descriptive zero-friction control; not realistic execution.")
    if name != "research_default":
        raise ValueError(f"unknown execution profile: {name}")
    return _RESEARCH_EXECUTION[key]


# Backward-compatible name used by earlier runtime modules.
InstrumentProfile = MarketProfile
PROFILES = MARKET_PROFILES
