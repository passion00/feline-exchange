# Native market data

Feline v0.11.4 uses primary provider-hosted data for new replication work:
Dukascopy BI5 ticks for EURUSD/XAUUSD and Binance Spot public daily kline
archives for BTCUSDT. Twelve Data import remains available for reproducibility,
but the failed April–September 2024 multi-market attempt is not an accepted
replication corpus.

Dukascopy hourly `h_ticks.bi5` files are LZMA streams of big-endian 20-byte
tick records. Feline aggregates the native **BID** ticks into UTC one-minute
OHLC. It never combines BID highs with ASK lows or manufactures a mid candle.
EURUSD uses the provider's 1e5 integer scale and XAUUSD uses 1e3. Missing
minutes remain missing.

Binance downloads `BTCUSDT` Spot **daily** 1m ZIPs from
`data.binance.vision`, plus every matching `.CHECKSUM`. A ZIP is parsed only
after its provider SHA-256 matches. OHLCV, quote volume, trade count, and taker
volumes are retained. BTCUSDT is not BTCUSD and uses the 24/7 calendar.

```bash
python3 -m feline data download --provider dukascopy --instrument EURUSD --start 2024-02-05T00:00:00Z --end 2024-03-02T00:00:00Z --output data/historical/processed/dukascopy_eurusd_2024-02-05_2024-03-02_1m.jsonl
python3 -m feline data download --provider binance --instrument BTCUSDT --start 2024-02-05T00:00:00Z --end 2024-03-02T00:00:00Z --output data/historical/processed/binance_btcusdt_2024-02-05_2024-03-02_1m.jsonl
python3 -m feline data quality DATASET --instrument BTCUSDT --start 2024-02-05T00:00:00Z --end 2024-03-02T00:00:00Z
```

Downloads are cached and atomically installed. Existing non-identical outputs
are not overwritten. Validation rejects malformed/non-finite/non-positive
OHLC, invariant violations, duplicates, backwards time, malformed duration,
and out-of-window bars. Expected FX/gold closures are distinguished from
unexpected gaps; crypto is 24/7. It never clamps, interpolates, forward-fills,
or back-fills. Statuses are `PASS`, `PASS_WITH_EXPECTED_CLOSURES`, `REVIEW`,
and `REJECTED`; a rejected quality sidecar blocks signal research.
