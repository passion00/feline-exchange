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
    provider: str = "managed_local"
    base_url: str = "http://127.0.0.1:8081"
    model: str = "feline/qwen3-4b-q4km"
    model_id: str | None = None
    request_timeout_seconds: float = 30.0
    retries: int = 1
    temperature: float = 0.1
    max_tokens: int = 600
    minimum_confidence: float = 0.65
    context_max_age_seconds: float = 20.0
    maximum_price_move_fraction: float = 0.001
    decision_mode: str = "advisory"
    queue_size: int = 32
    local_model_path: str | None = None
    llama_server_executable: str | None = None
    models_directory: str = "models"
    runtime_directory: str = "runtime/llama.cpp"
    preference_path: str = "data/ai_preferences.json"
    custom_model_path: str | None = None
    context_size: int = 8192
    threads: int | None = None
    gpu_layers: int | None = None
    startup_timeout_seconds: float = 30.0
    reasoning_mode: str = "disabled"

    def __post_init__(self) -> None:
        if self.provider not in {"openai_compatible", "llama_cpp", "managed_local", "local_llama_cpp"}:
            raise ValueError("unsupported AI provider")
        if self.decision_mode not in {"advisory", "record", "confirm_or_veto", "news_thesis"}:
            raise ValueError("invalid AI decision_mode")
        if not 0 <= self.temperature <= 2 or not 0 <= self.minimum_confidence <= 1:
            raise ValueError("invalid AI temperature/confidence")
        if self.request_timeout_seconds <= 0 or self.retries < 0 or self.max_tokens < 1 or self.queue_size < 1:
            raise ValueError("invalid AI resource bounds")
        if self.context_max_age_seconds <= 0 or self.maximum_price_move_fraction < 0:
            raise ValueError("invalid AI freshness bounds")
        if self.context_size < 512 or self.threads is not None and self.threads < 1 or self.gpu_layers is not None and self.gpu_layers < 0 or self.startup_timeout_seconds <= 0:
            raise ValueError("invalid managed local AI settings")


@dataclass(frozen=True)
class NewsConfig:
    enabled: bool = False
    provider: str = "rss"
    poll_interval_seconds: float = 60.0
    feed_urls: tuple[str,...] = ()
    dedup_history: int = 10_000
    queue_size: int = 128
    request_timeout_seconds: float = 10.0
    def __post_init__(self):
        if self.provider not in {"rss","fixture"}:raise ValueError("unsupported news provider")
        if self.poll_interval_seconds<=0 or self.dedup_history<1 or self.queue_size<1 or self.request_timeout_seconds<=0:raise ValueError("invalid news bounds")


@dataclass(frozen=True)
class ThesisConfig:
    minimum_confidence: float = .65
    minimum_relevance: float = .5
    maximum_active_theses: int = 16
    maximum_focused_instruments: int = 8
    default_expiry_minutes: float = 240.
    maximum_reference_move_fraction: float = .01
    def __post_init__(self):
        if not 0<=self.minimum_confidence<=1 or not 0<=self.minimum_relevance<=1:raise ValueError("invalid thesis score bounds")
        if self.maximum_active_theses<1 or self.maximum_focused_instruments<1 or self.default_expiry_minutes<=0 or self.maximum_reference_move_fraction<0:raise ValueError("invalid thesis resource bounds")


@dataclass(frozen=True)
class ConfirmationConfig:
    strategy: str = "reference_signal_alignment"
    minimum_signal_strength: float = .05
    reject_opposite_signal: bool = True
    def __post_init__(self):
        if self.strategy!="reference_signal_alignment" or not 0<=self.minimum_signal_strength<=1:raise ValueError("invalid confirmation configuration")


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
    news: NewsConfig = field(default_factory=NewsConfig)
    thesis: ThesisConfig = field(default_factory=ThesisConfig)
    confirmation: ConfirmationConfig = field(default_factory=ConfirmationConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    snapshot_interval_ticks: int = 20
    providers: dict = field(default_factory=dict)
    continuous: dict = field(default_factory=dict)
    markets: dict = field(default_factory=dict)
    execution_profiles: dict = field(default_factory=dict)
    continuous_risk_sizing: dict = field(default_factory=dict)


def load_config(path: Path | None = None) -> AppConfig:
    data = tomllib.loads(path.read_text()) if path and path.exists() else {}
    mode = data.get("mode", "paper")
    if mode != "paper":
        raise ValueError("Feline v0.1 supports paper mode only")
    continuous = dict(data.get("continuous", {}))
    continuous_risk_sizing = dict(continuous.pop("risk_sizing", {}))
    ai_data=dict(data.get("ai",{}));preference=Path(ai_data.get("preference_path","data/ai_preferences.json")).expanduser()
    if not preference.is_absolute():preference=(Path(__file__).resolve().parents[1]/preference).resolve()
    try:
        import json
        user_ai=json.loads(preference.read_text())
        for key in ("provider","base_url","model"):
            if key in user_ai:ai_data[key]=user_ai[key]
    except (OSError,ValueError):pass
    return AppConfig(
        mode=mode,
        database_path=str(data.get("database_path", "data/feline.db")),
        log_directory=str(data.get("log_directory", "logs")),
        tick_interval_seconds=max(0.01, float(data.get("tick_interval_seconds", 0.25))),
        risk=RiskConfig(**data.get("risk", {})),
        paper=PaperConfig(**data.get("paper", {})),
        ai=AIConfig(**ai_data),
        news=NewsConfig(**{**data.get("news",{}),"feed_urls":tuple(data.get("news",{}).get("feed_urls",()))}),
        thesis=ThesisConfig(**data.get("thesis",{})),
        confirmation=ConfirmationConfig(**data.get("confirmation",{})),
        strategy=StrategyConfig(**data.get("strategy", {})),
        snapshot_interval_ticks=max(1, int(data.get("snapshot_interval_ticks", 20))),
        providers=data.get("providers",{}),
        continuous=continuous,
        markets=data.get("markets",{}),
        execution_profiles=data.get("execution_profiles",{}),
        continuous_risk_sizing=continuous_risk_sizing,
    )
