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


@dataclass(frozen=True)
class PaperConfig:
    initial_cash: float = 100_000.0
    slippage_bps: float = 1.0


@dataclass(frozen=True)
class AIConfig:
    enabled: bool = True
    base_url: str = "http://127.0.0.1:8081"
    model: str = "llmware/qwen3-4b-instruct-gguf:Q4_K_M"
    request_timeout_seconds: float = 30.0
    queue_size: int = 32


@dataclass(frozen=True)
class AppConfig:
    mode: str = "paper"
    database_path: str = "data/feline.db"
    log_directory: str = "logs"
    tick_interval_seconds: float = 0.25
    risk: RiskConfig = field(default_factory=RiskConfig)
    paper: PaperConfig = field(default_factory=PaperConfig)
    ai: AIConfig = field(default_factory=AIConfig)


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
    )

