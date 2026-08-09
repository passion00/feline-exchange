# Feline Exchange v0.5 — Macro Event Research + Desktop Observer

Feline Exchange is a local-first observer, deterministic execution/replay simulator, and research platform. Version 0.5 remains paper/research only.

v0.5 studies EUR/USD after Fed/ECB-style shocks, deliberately avoiding the initial announcement race. It models deterministic pre-event, announcement, shock, stabilization, post-event, and complete phases; research decisions classify continuation, mean reversion, or explicit NO_TRADE.

Launch the local Tk desktop observer with `python3 -m feline gui`. It is marked PAPER / RESEARCH ONLY. The GUI is a thin read-only projection over core state and contains no trading or risk logic. Replay selection and deliberate-confirmation emergency stop are available; a display server is required.

Execution persistence uses one SQLite `BEGIN IMMEDIATE` boundary for order state, idempotent fills, cash, positions, remaining quantities, and protective state. `python3 -m feline doctor` performs read-only integrity diagnostics.

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

Market buys execute against ask and sells against bid. Market, limit, stop and stop-limit requests have accepted, partial, filled, cancelled, rejected and expired states. Quote arrival drives execution, so fixed or seeded-variable latency lets prices move before filling. Synthetic liquidity may split orders across quotes. This is an approximation, not an exchange order book.

Every fill records reference/fill price, spread, slippage, commission, latency and assumptions. Costs support flat, notional percentage, per-unit and minimum commissions. Generic FX financing supports long/short daily rates and triple-day rules. Defaults do not represent a specific broker.

Cash, positions, pending limits, remaining quantities and protective stop/take-profit prices are atomically persisted and restored. Replay end policies are `MARK_TO_MARKET`, `FORCE_CLOSE`, and `LEAVE_OPEN`; force-close uses simulated market orders and their costs.

Candle gaps support `SKIP` (default), `FORWARD_FILL` (last OHLC, zero volume/ticks) and `EMPTY_CANDLE` (same conservative numeric convention, zero volume/ticks). FX 24/5 and timezone/holiday-aware exchange calendar foundations are included.

Scheduled high/critical economic events create configurable before/after danger windows. New exposure can be blocked and position/exposure limits reduced. Post-event stabilization is represented by the after-window. AI results never affect this gate.

## Replay report limitations

Reports include return, realized P/L, trade counts, win/loss metrics, profit factor, drawdown, exposure time, return volatility, and a Sharpe-like sample metric. The Sharpe-like value is not annualized to a market calendar and has no risk-free-rate adjustment. Results inherit synthetic tick granularity, full-fill assumptions, and the configured paper slippage model.

v0.3 also itemizes gross/net P/L, commissions, spread, slippage, financing, expectancy, turnover, streaks and Sortino-like results. MAE/MFE and seeded trade-resampling utilities are research aids, not guarantees.

Research commands:

```bash
python3 -m feline replay tests/fixtures/multi_ticks.csv --speed max --seed 7
python3 -m feline experiment tests/fixtures/sample_ticks.csv --grid config/experiment-grid.example.toml --max-runs 8
python3 -m feline walk-forward tests/fixtures/sample_ticks.csv --train 6 --test 3
```

Provider TOML names credential environment variables only. Never place values in TOML. Providers use independent tasks and bounded queues. The optional metrics server binds `127.0.0.1`, accepts GET only, and exposes no controls.

**Simulation warning:** paper/backtest results do not predict or guarantee real-world profitability. The model lacks a real order book, venue queue position, hidden liquidity, and broker-specific rules.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Tests are offline and cover slow/failed AI, queue pressure, candles, indicators, regimes, event danger, shock volatility, protective orders, adverse slippage, recovery, replay, strategy provenance, reports, deduplication, provider failure, and kill switches.

Current provider research and licensing cautions are in [docs/DATA_PROVIDERS.md](docs/DATA_PROVIDERS.md). Included adapters are read-only Alpha Vantage FX polling and official-feed RSS/ECB news polling. They are not enabled by default; data API keys are never sent to AI.

## Lynx adaptation notes

Feline adapts Lynx's useful patterns rather than copying its project: local OpenAI-compatible llama.cpp HTTP integration, loopback defaults, dataclass configuration, parameterized SQLite, and local/offline operation. It intentionally does not reuse Lynx's conversation memory, summarization, fact extraction, Wikipedia, filesystem tools, permission router, TTS, chat orchestration, or large PySide6 GUI. Lynx's unbounded model request and blocking server startup are unsuitable for the real-time path; Feline instead uses a timeout-bounded background queue and starts safely without a model.

No GUI is included yet. A future dashboard should consume events/read models and remain outside the risk/execution authority path.
