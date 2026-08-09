# Historical macro research

Feline v0.9 measures the existing deterministic macro classifier across many FOMC and ECB episodes. It does not tune thresholds, place live orders, or claim that classifications imply profitability.

## Manifest and catalog

The manifest is versionable JSON with top-level `seed`, `split`, `contamination_policy`, default windows, and an `events` array. Each event provides `event_id`, `central_bank`, `event_type`, `title`, `instrument` or `instruments`, timezone-aware `timestamp`, `dataset_path`, source/region/importance, optional previous/consensus/actual/unit, notes/tags, and optional `secondary_events`. Missing economic values remain null; Feline never invents them.

Dataset paths may point to ignored local provider files. The registry records streaming SHA-256, provider, instrument, timeframe, historical bounds, native/reconstructed provenance, volume, spread provenance, import time, and storage notes. Exact duplicate market records, malformed OHLC, wrong instruments, missing files, and insufficient windows produce explicit exclusions.

## Episodes and time integrity

An episode contains one primary event, one dataset checksum, configurable pre/post windows (normally 60/120 minutes), and zero or more confounders. Native OHLC is replayed only at candle close. Pre-event features cannot see later bars, stabilization consumes observations sequentially, and horizon metrics appear only after their horizon elapsed.

If a secondary event lies inside a horizon, the default `flag` policy retains the raw measurement with `contains_secondary_event`. The optional `censor` policy retains the result for audit but sets `use_in_aggregate=false`. Nothing silently replaces the primary event or silently disappears.

## Partitions and reproducibility

Events are sorted by scheduled UTC time, then assigned chronological TRAIN, VALIDATION, and TEST partitions according to manifest fractions. No parameter selection occurs in v0.9. Experiment identity records Feline/Git state, manifest and configuration checksums, seed, risk/execution assumptions, event IDs, and dataset checksums. Equivalent inputs and commit produce equivalent event metrics; UUIDs and creation times are intentionally unique audit identity.

## Outputs and statistics

`events.csv` is one row per included primary event. `horizons.csv` is normalized: one row per event/horizon. Aggregate JSON includes robust descriptive statistics, MAE/MFE, positive/negative fractions, clean/contaminated counts, deterministic bootstrap intervals, stabilization durations, subgroup summaries, and an initial-shock continuation/reversal baseline. NO_TRADE reasons and descriptive `missed_move_candidate` labels are diagnostics—not strategy changes or failures.

Small samples, synthetic spreads, price/mid OHLC, provider timestamp conventions, confounding releases, and selection bias materially limit inference. Bootstrap intervals quantify resampling uncertainty only; they do not establish causal or future predictive validity.

## Example

```bash
python3 -m feline research validate tests/fixtures/research/manifest.json
python3 -m feline research run tests/fixtures/research/manifest.json
python3 -m feline research summarize data/reports/research/EXPERIMENT_ID
```

Downloaded market data and provider credentials are never committed. Review provider licensing before storing, sharing, or redistributing captures.
