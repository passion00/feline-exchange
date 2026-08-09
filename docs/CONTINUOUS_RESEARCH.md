# Continuous market research

Feline v0.11 evaluates every completed one-minute candle and is not centered only on macro announcements. It does not always trade: warmup, uncertain conditions, absent setups, existing instrument positions, and protected event windows all produce explicit `NO_TRADE` decisions.

```bash
python3 -m feline research continuous run DATASET.jsonl --instrument EURUSD --strategy all --no-trades
python3 -m feline research continuous run DATASET.jsonl --instrument EURUSD --strategy trend_pullback --seed 17
```

Outputs are checksum-addressed under `data/reports/continuous/` and contain `experiment.json`, `observation_schema.json`, `observations.csv`, `regimes.csv`, `signals.csv`, `trades.csv`, `summary.json`, and `summary.md`. The runner streams one dataset into incremental bounded state. Without `--no-trades`, eligible candidates are submitted to the existing deterministic `RiskEngine` and only approved requests reach `PaperBroker`; one-unit positions use fixed research stops and a deterministic 15-bar/event-risk/replay-end exit. This execution policy is deliberately simple and unoptimized. With `--no-trades`, only features, regimes, and setup decisions are produced.

## Time integrity and features

Provider candle times retain the existing semantics: a native one-minute candle becomes available only at its close time. Incomplete or non-one-minute decision candles are rejected. Every snapshot stores feature availability timestamps and fails if any timestamp exceeds `decision_timestamp`. Rolling state resets after a gap over two minutes and remains `WARMUP` until 61 completed observations exist. No candles are fabricated.

Returns are close-to-close fractions. Realized volatility is the population standard deviation of one-minute close returns. Normalized ranges are `(highest high - lowest low) / current close`. Trend slope is ordinary least-squares slope per bar divided by current price; price/MA is `close / mean(close) - 1`. Z-score uses population price variance and is null for zero variance. Range location is zero at the rolling low and one at the rolling high. Candle body/wicks use the v0.10 high-low fractions. UTC sessions are Asia 00:00–07:00, London 07:00–12:00, London/New York overlap 12:00–16:00, New York 16:00–21:00, and off-hours otherwise.

Forward 5m/15m/30m returns are explicitly marked future labels in `observation_schema.json`. They are calculated only after all predictor/regime/router processing and can never enter a decision.

## Regimes and precedence

The deterministic precedence is `EVENT_RISK`, `VOLATILITY_EXPANSION`, `VOLATILITY_COMPRESSION`, directional trend, `RANGING`, then `UNCERTAIN`. `WARMUP` is used before sufficient continuous history. Strength is a bounded diagnostic ratio, not a probability.

Critical scheduled events activate `EVENT_RISK` in the configured before/after window. That suppresses every ordinary strategy and routes only to the unchanged macro-event family. After the window, ordinary classification resumes.

The reference hypotheses are:

- `trend_pullback`: modest counter-trend movement within an established directional regime.
- `range_mean_reversion`: displacement toward a recent range edge while the regime remains ranging.
- `volatility_breakout`: a completed breakout after a prior compression observation and current expansion.

The routing priority is explicit through regime eligibility. One instrument position suppresses conflicting ordinary candidates. Defaults in `[continuous]` are fixed initial research assumptions, not optimized parameters.

## Limitations

The v0.11 runner is a feature/regime/setup research framework, not evidence of an edge. It does not optimize, fit ML, use AI, claim profitability, or execute candidate orders. Forward-return controls are descriptive. A first real EURUSD study should use several continuous weeks of licensed native one-minute OHLC spanning weekdays, weekends, ordinary sessions, and scheduled events—not the narrow FOMC episode windows.
