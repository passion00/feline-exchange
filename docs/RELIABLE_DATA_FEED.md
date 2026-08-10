# Reliable production data feed

Feline v0.12 separates provider transport from normalized completed candles and
ticks. `HistoricalDataProvider` and `RealtimeDataProvider` advertise explicit
capabilities and are selected through `DataFeedRegistry`.

## Provider policy

- **Primary FX: OANDA v20.** Historical acquisition requests unsmoothed,
  completed M1 candles (`smooth=false`) and records whether MID, BID, or ASK was
  requested. The same adapter parses the official pricing stream's timestamped
  bid/ask levels and ignores heartbeats. It is read-only and has no order code.
- **Historical audit/fallback: Dukascopy.** Provider-native BID ticks are
  aggregated into completed UTC minutes. Missing ticks/minutes are not filled.
- **Crypto: Binance Spot public archives.** BTCUSDT daily ZIPs must pass their
  published SHA-256 before parsing.
- **Legacy: Twelve Data.** Existing files/imports remain reproducible, but it is
  not the preferred production or replication feed.

OANDA requires `FELINE_OANDA_API_TOKEN`; realtime also requires
`FELINE_OANDA_ACCOUNT_ID`. Secrets are sent only in the Authorization header,
never placed in URLs, exceptions, provenance, logs, or event payloads.

## Integrity contract

Historical validation checks parseability, finite positive prices, OHLC
relationships, exact one-minute duration, UTC-aware ordering, duplicates,
backwards timestamps, requested exclusive boundaries, and market-aware gaps.
It emits `PASS`, `PASS_WITH_EXPECTED_CLOSURES`, `REVIEW`, or `REJECTED`. Nothing
is clamped, interpolated, forward-filled, or back-filled. Rejected sidecars
block signal research. `data audit` independently reconciles processed and
quality checksums with provider provenance.

Realtime quotes are rejected when crossed, non-positive, future-dated,
duplicate/reordered, or older than the configured stale interval. Transport
uses bounded retries, exponential backoff, rate limiting, timeouts, and
sanitized errors. Provider failure is observable; it never invents a quote.

## Commands

```bash
export FELINE_OANDA_API_TOKEN=...
python3 -m feline data download --provider oanda --instrument EURUSD \
  --price-basis mid --start 2024-07-15T00:00:00Z \
  --end 2024-07-16T00:00:00Z --output data/historical/processed/oanda_eurusd_2024-07-15.jsonl
python3 -m feline data quality data/historical/processed/oanda_eurusd_2024-07-15.jsonl \
  --instrument EURUSD --start 2024-07-15T00:00:00Z --end 2024-07-16T00:00:00Z
python3 -m feline data audit data/historical/processed/oanda_eurusd_2024-07-15.jsonl --instrument EURUSD
```

Cached outputs are reused only when request identity and checksum match.
Non-identical existing datasets are never overwritten. Provider files and
large processed datasets remain ignored local artifacts.
