# News Intelligence Experiments

Feline v0.17.2 automates evaluation of the production path:

`NewsEvent → AI schema validation → MarketThesis → InstrumentUniverse → FocusManager → completed-candle confirmation → RiskEngine`

The harness is not a second intelligence engine. It creates an isolated runtime, calls `FelineRuntime.submit_news`, waits for the normal asynchronous AI worker, and supplies deterministic ticks through `FelineRuntime.handle_tick`. Production persistence, bounded-universe validation, focus lifecycle, one-minute candle aggregation, reference strategy, and risk checks therefore remain authoritative.

## Safety and semantics

Engineering validation is strict PASS/FAIL. Unknown symbols, direct action fields, malformed schemas, duplicate news, unavailable/non-shortable assets, expiry, stale moves, feed degradation, emergency stop, RiskEngine rejection, persistence, and external-order isolation are invariants.

Intelligence quality is descriptive. A case declares acceptable instruments and directions in scoring metadata that is never sent to the model. Results are `strong_match`, `match`, `partial_match`, `mismatch`, `abstained`, or `unsupported`, with visible instrument, direction, and relevance components. Nondeterministic but reasonable wording is not a software failure.

## Commands

```bash
# Fully deterministic; no network/model call
python3 -m feline experiment news-intelligence \
  --suite standard --ai fixture \
  --report data/experiments/fixture-standard

# Managed-local smoke suite; assets must already be installed
python3 -m feline experiment news-intelligence \
  --suite smoke --ai local --start-ai \
  --report data/experiments/qwen-smoke

# Compare reports
python3 -m feline experiment compare \
  data/experiments/qwen/report.json \
  data/experiments/other/report.json \
  --report data/experiments/comparison.json
```

External mode uses `--ai external` and an external OpenAI-compatible provider configured in `[ai]`. Selectors include `--case`, `--category`, `--limit`, `--seed`, `--no-price-scenarios`, `--format`, `--fail-on-safety-error`, and `--resume RUN_DIRECTORY`. Resume reads completed `cases.jsonl` records and processes missing cases. The legacy `feline experiment DATASET --grid GRID.toml` command remains supported.

Local/external news cases inherit `news_thesis_timeout_seconds`, which defaults to 300 seconds. `--ai-timeout SECONDS` is an explicit per-run override; no temporary configuration file is required. Fixture failure injection uses a deliberately tiny mocked deadline so tests never sleep for minutes.

## Corpus and scoring

The human-readable corpus is `feline/resources/news_benchmark_standard.jsonl`. Each row contains source news, a bounded mock instrument universe, semantic expectations, fixture analysis, optional price scenario, and safety expectations. Expected answers and fixture analyses are never inserted into AI prompts. `standard` has 31 energy, macro, geopolitical, company, commodity, relevance, capability, duplication, and hostile-input cases; `smoke` selects four; `safety` selects capability and hostile-input cases.

Fixture AI is deterministic and offline. Local and external modes use the existing OpenAI-compatible client. Reports capture normalized and bounded raw JSON, provider/model, prompt/context hashes, validation failures, scores, lifecycle, and latency—not credentials or giant prompt copies.

Semantic score weights are explicit: acceptable instrument coverage 50%, direction 35%, and affected-asset relevance 15%. Aggregate reports separately expose irrelevant-news false positives, relevant thesis rate, abstention, unsupported proposals, direction tables, schema failures, and latency. This is intentionally not a hidden LLM judge.

A transport timeout is `AI status: TIMEOUT` and semantic `not_evaluated`, never `abstained`. Schema is `NOT_EVALUATED`; thesis persistence and lifecycle are `NOT_APPLICABLE`. Safety remains independently PASS when no prohibited order occurs, preventing one root-cause timeout from being misreported as several software failures.

## Prices, isolation, and reports

Deterministic generators provide upward/downward confirmation, flat prices, non-directional volatility, reversal, post-expiry confirmation, and excessive moves. The model never creates prices. Descriptive 5/15/30/60-minute returns never modify decisions.

Every run writes to ignored `data/experiments/` by default:

- `experiment.db`: isolated SQLite state;
- `cases.jsonl`: incremental/resumable results;
- `report.json`: machine-readable provenance, metrics, and cases;
- `report.md`: readable per-case lifecycle.

The runner cannot accept an external execution adapter. It uses internal PaperBroker with autonomous execution disarmed. Explicit rejection cases may arm only the internal broker to demonstrate deterministic blocking. Normal broker profiles, positions, watchlists, databases, and emergency-stop state are untouched.

Live RSS observation and a GUI results panel are deferred; existing event/report hooks are reusable. A benchmark score does not imply profitability. Correct news interpretation does not imply profitable execution, and post-news movement does not prove causality.

## Output-state accounting (v0.17.4)

- `TIMEOUT` or `TRANSPORT_ERROR`: no response schema was evaluated.
- `INVALID_JSON`: transport completed but no single parseable JSON object was returned.
- `INVALID_SCHEMA`: JSON arrived but failed the authoritative contract.
- `VALID`: schema validation succeeded, including a deliberate empty affected-instrument list.
- semantic `abstained`: only a valid response intentionally returned no affected instruments; it scores `1.0` when no impact is expected and `0.0` when relevant impact was missed. Invalid output is always `not_evaluated`.
- thesis persistence/lifecycle are `NOT_APPLICABLE` after upstream invalidity; safety is still evaluated independently.

Summary accounting exposes transport successes/errors, schema-valid and invalid response counts, timeouts, and a request-reconciliation invariant. Unsupported symbols from invalid raw output remain useful quality diagnostics but never enter semantic scoring.

The v0.17.3 31-case Qwen run had 22 schema failures: 15 cases used string monitoring priorities, 6 used numeric ranks above one, two cases also used string relevance, and one no-impact case selected `UNKNOWN` instead of abstaining (categories overlap). No validator rule was relaxed. v0.17.4 clarifies the generic contract and uses managed-local constrained generation; benchmark expectations remain evaluation-only and are never included in prompts.

The final v0.17.4 local-Qwen run (`data/experiments/qwen-v0174-standard/`) produced 31/31 schema-valid responses, zero timeouts, zero unsupported proposals, zero safety/lifecycle failures, and zero orders. Semantic outcomes were 7 strong matches, 8 partial matches, 3 mismatches, and 13 valid abstentions; mean latency was 22.0 seconds and p95 was 31.0 seconds. The evaluated irrelevant false-positive rate fell from 0.60 to 0.375, but prompt-injection-like text still caused genuine semantic false positives in three cases. These are model-quality limitations, not schema or execution-safety failures.
