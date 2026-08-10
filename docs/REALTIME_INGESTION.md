# Realtime paper ingestion

In v0.14, realtime paper mode may asynchronously request structured AI confirmation for deterministic signals. This does not change ingestion, completed-candle, feed-health, risk, or PaperBroker authority; see [AI Integration](AI_INTEGRATION.md).

Feline v0.13 uses the read-only OANDA v20 pricing stream as its primary FX
source. This is market ingestion only: every resulting order still passes
through the existing deterministic `RiskEngine` and `PaperBroker`; there is no
live broker/order adapter.

```bash
export FELINE_OANDA_API_TOKEN=...
export FELINE_OANDA_ACCOUNT_ID=...
python3 -m feline realtime start --instrument EURUSD
# from another terminal
python3 -m feline realtime stop
```

`--duration SECONDS` provides a bounded verification session. `--environment
practice` is the default; selecting `live` changes only the read-only price
host and does not create a live-order path. The GUI's **Start OANDA Paper**
button starts the same off-GUI-thread runtime.

## Integrity and timing

OANDA source timestamps remain `PriceTick.timestamp`; Feline adds a separate
UTC ingestion timestamp, stable realtime session ID, and monotonic provider
sequence. Crossed, non-positive, stale, future, duplicate, and reordered quotes
are rejected before the runtime. Heartbeats keep transport alive but never
become prices. A missing quote beyond the feed timeout produces `STALE`; stream
errors produce `DEGRADED`, with bounded exponential reconnect delay.

Only a valid quote can restore `HEALTHY`. New orders are rejected by an
additional deterministic market-feed gate whenever state is not healthy.
Therefore stale cached quotes cannot trigger strategies or execution.

UTC candles use bid/ask midpoint observations. A candle becomes complete only
when a valid quote enters a later time bucket; shutdown flushes active buckets
as explicitly incomplete and never runs strategy logic on them. Completed
candles follow the same indicator, regime, reference strategy, risk and paper
broker path used by replay.

SQLite stores session metadata, source and ingestion timestamps, accepted
quotes, completed candles, signals, risk decisions, orders, fills, positions,
portfolio snapshots, and feed-health events. Broker state and pending/protective
state are committed using the existing atomic recovery unit during shutdown.

Realtime mode deliberately disables AI jobs and adds no news, strategy,
threshold, risk, or execution changes.
