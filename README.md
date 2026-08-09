# Feline Exchange v0.2 — Market Observer, Replay Engine + Advanced Paper Trader

Feline Exchange is a local-first market observer, deterministic replay engine, and quantitative paper trader. Version 0.2 cannot submit real orders: the only broker is `PaperBroker`, and configuration rejects every mode except `paper`.

## Three-speed architecture

```text
Provider or CSV replay -> ticks -> multi-timeframe candles
                              -> Level 2 indicators/regime/reference signal
                              -> Level 1 danger/risk gate -> PaperBroker -> SQLite audit
News -> normalize/deduplicate/tag/score -> bounded priority AI queue -> validated result
```

Level 1 never calls or waits for an LLM. Level 2 performs ordinary numerical work. Level 3 is optional, slow, bounded, priority-aware, and failure-tolerant. Replay deliberately uses the same `FelineRuntime.handle_tick()` path as observer mode.

## Start

Python 3.11+ is required; v0.1 has no third-party runtime dependencies.

```bash
cp config/feline.example.toml config/feline.toml
python3 -m feline status
python3 -m feline paper
```

For a short observer smoke run:

```bash
python3 -m feline paper --duration 3
```

Offline replay with readable and JSON output:

```bash
python3 -m feline replay tests/fixtures/sample_ticks.csv --speed max --strategy reference --report data/report.json
```

Install an optional CLI entry point with `python3 -m pip install -e .`, then use `feline paper`. AI defaults to Lynx's local llama.cpp-compatible endpoint at `127.0.0.1:8081`, but Feline does not start or require that server.

Emergency stop creates an ignored persistent marker, is detected by a running market loop on its next tick, and blocks later starts:

```bash
python3 -m feline stop
```

Remove `data/EMERGENCY_STOP` only after deliberate operator review to re-enable startup. In-process limits can also activate the risk engine's global kill switch.

## Packages

- `core`: immutable typed events and async publish/subscribe
- `market`: provider interfaces and offline mock ticks
- `market`: tick providers and UTC 1m/5m/15m/1h candle aggregation
- `quant`: SMA, EMA, RSI, ATR, volatility, momentum, velocity, ranges, drawdown, spread, regimes
- `strategy`: versioned reference SMA/momentum strategy producing typed signals only
- `risk`: limits, event danger windows, volatility protection, and kill switch
- `execution`: market paper fills, cancellation, position close, stop-loss/take-profit triggers
- `portfolio`: positions and realized P/L accounting
- `replay`: streaming CSV input and deterministic performance reporting
- `news`: normalization, deduplication, entity tags, relevance and priority
- `intelligence`: bounded priority jobs, local llama.cpp client, strict output validation
- `storage`: WAL-mode SQLite and ordered schema migrations

The schema also stores candles, regime transitions, health state, positions, and periodic portfolio snapshots. Startup restores the latest cash and position state.

## Paper execution assumptions

Market buys fill from ask and sells from bid, plus directional configured slippage. Stops and targets are checked on incoming quotes. A gap fills at the observed executable side plus adverse slippage—not the requested stop. Extreme regimes multiply slippage. Fills are currently complete and immediate; latency, commissions, financing, queue priority, partial fills, and full limit-order simulation are not yet modeled. `OrderType.LIMIT` reserves the interface but is not simulated.

Scheduled high/critical economic events create configurable before/after danger windows. New exposure can be blocked and position/exposure limits reduced. Post-event stabilization is represented by the after-window. AI results never affect this gate.

## Replay report limitations

Reports include return, realized P/L, trade counts, win/loss metrics, profit factor, drawdown, exposure time, return volatility, and a Sharpe-like sample metric. The Sharpe-like value is not annualized to a market calendar and has no risk-free-rate adjustment. Results inherit synthetic tick granularity, full-fill assumptions, and the configured paper slippage model.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Tests are offline and cover slow/failed AI, queue pressure, candles, indicators, regimes, event danger, shock volatility, protective orders, adverse slippage, recovery, replay, strategy provenance, reports, deduplication, provider failure, and kill switches.

Current provider research and licensing cautions are in [docs/DATA_PROVIDERS.md](docs/DATA_PROVIDERS.md). Included adapters are read-only Alpha Vantage FX polling and official-feed RSS/ECB news polling. They are not enabled by default; data API keys are never sent to AI.

## Lynx adaptation notes

Feline adapts Lynx's useful patterns rather than copying its project: local OpenAI-compatible llama.cpp HTTP integration, loopback defaults, dataclass configuration, parameterized SQLite, and local/offline operation. It intentionally does not reuse Lynx's conversation memory, summarization, fact extraction, Wikipedia, filesystem tools, permission router, TTS, chat orchestration, or large PySide6 GUI. Lynx's unbounded model request and blocking server startup are unsuitable for the real-time path; Feline instead uses a timeout-bounded background queue and starts safely without a model.

No GUI is included yet. A future dashboard should consume events/read models and remain outside the risk/execution authority path.
