# Historical macro research

Feline v0.9.1 measures the existing deterministic macro classifier across many FOMC and ECB episodes. It does not tune thresholds, place live orders, or claim that classifications imply profitability.

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

## Post-shock measurement bases

The original `announcement` horizon rows remain unchanged and use the pre-event reference. New `one_minute` rows use the first completed observation at or after announcement +1 minute, so `one_minute` 15m means `price_at_announcement_plus_15m / price_at_announcement_plus_1m - 1`. New `stabilization` rows start at the completed observation where deterministic stabilization was detected and target 5/15/30/60 elapsed minutes afterward. Missing targets remain absent; Feline never extrapolates.

The stabilization impulse is `stabilization_price - pre_event_price`. At the longest available configured stabilization horizon:

- impulse retention = `(later_price - pre_event_price) / stabilization_impulse`
- retracement = `1 - impulse_retention`

Thus retention 1/retracement 0 means no retracement, retention 0/retracement 1 means a full return to the pre-event reference, negative retention means reversal beyond it, and retention above 1 means extension. Values are intentionally not clamped.

Post-stabilization MAE/MFE use native intrabar low/high where available. Values are signed in the initial-shock direction: MFE is the largest favorable directional return from stabilization; MAE is the smallest directional return and is normally negative. Extension/reversal use the same direction but normalize price displacement by the pre-event reference. Times to high/low/extreme are measured from announcement, and do not imply that an intrabar extreme was a completed close.

`post_stabilization_outcome` is descriptive, separate from `strategy_outcome`, and evaluated at the configured 15-minute post-stabilization horizon by default. Absolute return at or below `post_stabilization_flat_tolerance` (default 0.001) is FLAT; otherwise sign agreement with the stabilization impulse is CONTINUATION and disagreement is MEAN_REVERSION. An event without detected stabilization is NO_STABILIZATION and receives no fabricated stabilization metrics.

Contamination is calculated independently for each actual interval. A +30-minute press conference can leave stabilization→15m clean while contaminating stabilization→30m. `flag` retains it; `censor` excludes it only from aggregate calculation.

NO_TRADE exports include every unchanged strategy gate's observed value, threshold, comparison and pass/fail state. These diagnostics explain decisions but do not participate in them.

Schema 1.1 keeps legacy event payloads readable and adds `research_post_shock_metrics`, keyed by experiment and event. `events.csv` exposes common post-shock fields directly. `horizons.csv` adds a `reference_basis` discriminator (`announcement`, `one_minute`, or `stabilization`) plus explicit reference/target timestamps; existing announcement returns retain their v0.9 meaning.

Small samples, synthetic spreads, price/mid OHLC, provider timestamp conventions, confounding releases, and selection bias materially limit inference. Bootstrap intervals quantify resampling uncertainty only; they do not establish causal or future predictive validity.

## Example

```bash
python3 -m feline research validate tests/fixtures/research/manifest.json
python3 -m feline research run tests/fixtures/research/manifest.json
python3 -m feline research summarize data/reports/research/EXPERIMENT_ID
```

Downloaded market data and provider credentials are never committed. Review provider licensing before storing, sharing, or redistributing captures.

## Corpus automation

The corpus builder separates mechanical data preparation from outcome research. Without `--run` it only acquires, validates, converts and validates a manifest; it never runs or summarizes an experiment. Twelve Data credentials come only from `FELINE_TWELVE_DATA_API_KEY` and are never written to artifacts or logs.

```bash
export FELINE_TWELVE_DATA_API_KEY=your_key_here
python3 -m feline research corpus build --central-bank FOMC --years 2023 --instrument EURUSD --provider twelvedata
python3 -m feline research corpus build --central-bank FOMC --years 2023 --instrument EURUSD --provider twelvedata --run
python3 -m feline research corpus doctor --years 2022 2024 --instrument EURUSD
python3 -m feline research compare data/reports/research/EXPERIMENT_A data/reports/research/EXPERIMENT_B
```

`--dry-run` reports missing acquisitions without writes. `--skip-download` validates and converts local raw files without network access. `--force-download` explicitly refetches and preserves the prior raw file under a checksum-named backup.

Raw quality checks use Decimal OHLC validation, duplicate detection, non-positive-price checks and exact one-minute gaps. One bounded recheck distinguishes transient from persistent provider gaps. Persistent gaps within announcement −5m through +30m quarantine the event; gaps outside that interval remain visible as `persistent_noncritical_gap`. Provider OHLC anomalies are preserved for operator review and never silently repaired.

Each year receives `data_quality.json` and a v0.9.1-compatible manifest. Corpus doctor is read-only. Experiment comparison reads existing artifacts and adds direction-normalized clean post-stabilization 5m/15m returns solely as reporting metrics: positive means movement with the initial shock and negative means reversal. Mechanical quality status is not a strategy outcome and does not imply profitability.
