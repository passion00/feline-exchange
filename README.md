# Feline Exchange v0.15.0 — Live Paper Validation & AI Evaluation

Feline Exchange is a local-first observer, deterministic execution/replay simulator, and market research platform. Version 0.15 adds controlled realtime paper-validation sessions and objective AI evaluation exports around the v0.14 reasoning layer. The model may confirm or veto an existing deterministic signal, but it cannot originate orders or bypass feed health, RiskEngine, PaperBroker, stops, exposure, or the kill switch. See [Live Paper Validation](docs/LIVE_PAPER_VALIDATION.md), [AI Integration](docs/AI_INTEGRATION.md), and [Realtime paper ingestion](docs/REALTIME_INGESTION.md). No live-order path exists.

AI configuration is provider-neutral at the trading layer. The included practical backend speaks the OpenAI-compatible chat-completions protocol (including local `llama.cpp`); invalid, stale, low-confidence, unavailable, or contradictory assessments fail closed without blocking market ingestion.

Continuous multi-market examples:

```bash
python3 -m feline research continuous run DATASET --instrument XAUUSD --strategy all --sizing risk --risk-fraction 0.0025 --starting-equity 100000 --execution-profile research_default --seed 17
python3 -m feline research continuous run DATASET --instrument XAUUSD --strategy all --sizing risk --execution-profile reference_zero_cost --seed 17
python3 -m feline research continuous compare REPORT_EURUSD REPORT_XAUUSD REPORT_BTCUSD --comparison-basis native
```

`reference_zero_cost` is a frictionless descriptive control, never a realistic execution claim. All research-default execution profiles are explicitly uncalibrated.

Signal-locked research:

```bash
python3 -m feline research signals run data/historical/processed/eurusd_4weeks.jsonl --instrument EURUSD --strategy all --starting-equity 100000 --risk-fraction 0.0025 --execution-profile research_default --cost-multipliers 0,0.25,0.5,0.75,1,1.5,2 --seed 17
python3 -m feline research signals compare STUDY_EURUSD STUDY_XAUUSD STUDY_BTCUSD --comparison-basis native
```

This is predictive-signal research, not a portfolio backtest. See `docs/SIGNAL_LOCKED_RESEARCH.md`.

v0.8 studies EUR/USD after Fed/ECB-style shocks, deliberately avoiding the initial announcement race. The Qt workstation opens CSV tick data and globally ordered mixed JSONL price/macro fixtures through one replay control. It models deterministic pre-event, announcement, shock, stabilization, post-event, and complete phases; research decisions classify continuation, mean reversion, or explicit NO_TRADE.

Install with `python3 -m pip install -e .`, then launch with `python3 -m feline gui`. Open a CSV fixture, choose speed, and Start. The real `FelineRuntime` runs in a worker executor; typed events enter a bounded, nonblocking projection queue. Qt renders actual prices, watchlist values, portfolio, risk, AI availability, and filtered event history. Pause/resume/stop and consecutive replays work without restarting. Emergency stop activates both the in-process risk kill switch and persistent marker. Rendering is deliberately lossy under pressure; SQLite/core processing remains authoritative.

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

Python 3.11+ is required. PySide6 and pyqtgraph are declared project dependencies.

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

## Qt macro workstation

The existing dark Qt layout is retained. `Open Dataset` accepts CSV ticks and the `fed_macro`, `macro_continuation`, `macro_mean_reversion`, and `macro_no_trade` JSONL fixtures. Watchlist clicks select the chart without resetting replay. The chart fits once on first data, dataset reset, or instrument switch; `Fit` restores automatic range after manual zoom/pan. Horizon values appear progressively and remain `not reached` until measured.

Signals, simulated orders/fills, durable completed trades, diagnostics, portfolio, risk, AI availability, and NO_TRADE counts are projected from runtime state. Closing the window stops workers and closes SQLite. Qt has no broker, strategy, or risk authority.

## Replay sessions and reports

Every replay receives a persisted UUID plus dataset SHA-256, historical source-time range, instruments, strategy mode, risk configuration, and execution assumptions. Starting another replay clears only transient GUI projections. SQLite research history is retained but is never merged into the active Signals, Orders/Fills, Completed Trades, Horizons, markers, or diagnostics views. Mixed macro JSONL runs default to `macro_only`, disabling reference-strategy order generation for that session; generic CSV replay retains the reference strategy.

After completion, **Export Replay Report** writes a non-overwriting JSON report and concise Markdown companion. Both contain the active session only. The CLI uses the same builder:

```bash
python3 -m feline replay tests/fixtures/fomc_2024_synthetic.jsonl --speed max --report data/reports/fomc_2024.json
```

Projected rows distinguish historical `source_timestamp` from wall-clock `ingestion_timestamp`. The latter is audit metadata and is never displayed as the historical event time.

## Native OHLC and Twelve Data local import

`CandleUpdate` validates OHLC ordering and identifies `native` versus tick-`reconstructed` provenance. A provider candle's datetime is modeled as `open_time`; its complete OHLC becomes available only at `close_time`, which is also its replay timestamp. Native 1-minute bars aggregate deterministically into completed 5m, 15m, and 1h bars. Incomplete higher-timeframe buckets are not flushed, preventing future-data leakage.

```bash
python3 -m feline import-twelvedata data/historical/fed/eurusd_2024-09-18.json data/historical/fed/eurusd_2024-09-18-ohlc.jsonl --instrument EURUSD --interval 1min --timezone UTC
python3 -m feline add-macro-event data/historical/fed/eurusd_2024-09-18-ohlc.jsonl data/historical/fed/eurusd_2024-09-18-fomc.jsonl --timestamp 2024-09-18T18:00:00Z --event-id fomc-2024-09-18 --title "FOMC September 2024 decision" --instrument EURUSD
python3 -m feline gui
```

The chart defaults to **Candles**, offers Line mode and 1m/5m/15m/1h selectors, and retains zoom, pan, Fit, instrument selection, date/time axes, and markers. Clicking a candle shows its completed O/H/L/C and volume.

Horizon return, volatility, shock, and stabilization retain close-to-close semantics. Native OHLC lets horizon MAE use intrabar lows and MFE use intrabar highs; no strategy threshold was retuned. Provider OHLC is price/mid data—not historical bid/ask. Paper execution derives bid/ask from the configured synthetic spread, and reports label that assumption. Downloaded provider files remain local/ignored and subject to provider licensing terms.

## Historical macro batch research

Version 0.9 adds a JSON event manifest, checksum-backed dataset registry, episode builder, chronological TRAIN/VALIDATION/TEST membership, secondary-event contamination flags, seeded batch execution, and experiment exports. Batch runs use the unchanged `macro_event` strategy in `macro_only` mode; they measure the current system and do not optimize it.

```bash
python3 -m feline research validate tests/fixtures/research/manifest.json
python3 -m feline research inspect tests/fixtures/macro_continuation.jsonl --instrument EURUSD
python3 -m feline research run tests/fixtures/research/manifest.json --output-root data/reports/research
python3 -m feline research summarize data/reports/research/EXPERIMENT_ID
python3 -m feline research import-directory data/historical/raw data/historical/processed --instrument EURUSD --interval 1min
```

The workstation's **Run Research** action selects a manifest and runs outside the Qt thread. Its Research panel shows experiment ID, current event, completed/total, and exclusions; **Cancel Batch** preserves every committed episode.

Each experiment directory contains `experiment.json`, `summary.md`, `events.csv`, `horizons.csv`, and `exclusions.csv`. Event data is processed one episode at a time. Native candles remain invisible until close, horizon outcomes are only computed after elapsed historical time, and TEST membership is explicit rather than randomly sampled. See [Historical research](docs/HISTORICAL_RESEARCH.md).

## Continuous market research

The additive v0.11 engine evaluates completed one-minute bars through compact rolling features, deterministic regimes, and explicit `NO_TRADE` routing. Critical event windows take precedence and suppress ordinary strategy families without changing macro logic.

```bash
python3 -m feline research continuous run data/historical/processed/eurusd_week.jsonl --instrument EURUSD --strategy all --no-trades
python3 -m feline research continuous run data/historical/processed/eurusd_week.jsonl --instrument EURUSD --strategy all --seed 17
```

The first command produces feature/regime/setup observations only. The second sends eligible paper candidates through the existing deterministic risk engine and PaperBroker. Neither mode is live trading. See [Continuous research](docs/CONTINUOUS_RESEARCH.md) for formulas, precedence, configuration, and limitations.
