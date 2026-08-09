from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class RiskConfig:
    max_position_size: float = 100.0
    max_total_exposure: float = 100_000.0
    max_loss_per_trade: float = 1_000.0
    max_daily_loss: float = 2_500.0
    max_drawdown: float = 0.10
    max_allowed_spread: float = 0.01
    emergency_volatility_threshold: float = 0.10
    trading_enabled: bool = True
    event_minutes_before: float = 30.0
    event_minutes_after: float = 30.0
    event_position_factor: float = 0.25
    event_exposure_factor: float = 0.25
    event_block_new_positions: bool = True


@dataclass(frozen=True)
class PaperConfig:
    initial_cash: float = 100_000.0
    slippage_bps: float = 1.0
    volatility_slippage_multiplier: float = 5.0
    spread_slippage_factor: float = 0.05
    size_slippage_bps_per_unit: float = 0.0
    fixed_latency_ms: float = 0.0
    variable_latency_ms: float = 0.0
    random_seed: int = 0
    liquidity_fraction: float = 1.0
    flat_commission: float = 0.0
    percentage_commission: float = 0.0
    per_unit_commission: float = 0.0
    minimum_commission: float = 0.0
    replay_end_policy: str = "MARK_TO_MARKET"
    synthetic_spread_bps: float = 2.0


@dataclass(frozen=True)
class AIConfig:
    enabled: bool = True
    base_url: str = "http://127.0.0.1:8081"
    model: str = "llmware/qwen3-4b-instruct-gguf:Q4_K_M"
    request_timeout_seconds: float = 30.0
    queue_size: int = 32


@dataclass(frozen=True)
class StrategyConfig:
    enabled: bool = True
    fast_period: int = 3
    slow_period: int = 5
    atr_period: int = 3
    risk_fraction: float = 0.002
    max_signal_quantity: float = 10.0


@dataclass(frozen=True)
class AppConfig:
    mode: str = "paper"
    database_path: str = "data/feline.db"
    log_directory: str = "logs"
    tick_interval_seconds: float = 0.25
    risk: RiskConfig = field(default_factory=RiskConfig)
    paper: PaperConfig = field(default_factory=PaperConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    snapshot_interval_ticks: int = 20
    providers: dict = field(default_factory=dict)
    continuous: dict = field(default_factory=dict)


def load_config(path: Path | None = None) -> AppConfig:
    data = tomllib.loads(path.read_text()) if path and path.exists() else {}
    mode = data.get("mode", "paper")
    if mode != "paper":
        raise ValueError("Feline v0.1 supports paper mode only")
    return AppConfig(
        mode=mode,
        database_path=str(data.get("database_path", "data/feline.db")),
        log_directory=str(data.get("log_directory", "logs")),
        tick_interval_seconds=max(0.01, float(data.get("tick_interval_seconds", 0.25))),
        risk=RiskConfig(**data.get("risk", {})),
        paper=PaperConfig(**data.get("paper", {})),
        ai=AIConfig(**data.get("ai", {})),
        strategy=StrategyConfig(**data.get("strategy", {})),
        snapshot_interval_ticks=max(1, int(data.get("snapshot_interval_ticks", 20))),
        providers=data.get("providers",{}),
        continuous=data.get("continuous",{}),
    )
