# News-driven market intelligence

Automated evaluation is documented in [News Intelligence Experiments](EXPERIMENTS.md). Benchmark expectations are never sent to the AI, and fixture/local/external modes all enter the production normalization, validation, thesis, focus, confirmation, and risk path.

News-market-impact inference may intentionally take up to 300 seconds on local CPU hardware. Publication/ingestion time, expected horizon, and `MarketThesis.expires_at` determine news/thesis freshness—not model latency. If price moves during analysis, the valid thesis is still recorded and focused using the current quote. Later deterministic confirmation may reject an entry as `STALE_MOVE`; it does not erase the thesis. RiskEngine remains the final authority.

Feline v0.17 implements:

```text
RSS / fixture / economic event
  -> normalized NewsEvent (publication and ingestion timestamps)
  -> bounded asynchronous AI job (`news-market-impact-v1`)
  -> persisted MarketThesis over the supplied InstrumentUniverse
  -> bounded FocusManager
  -> completed-candle reference-signal alignment
  -> feed health + autonomous Start/Stop + RiskEngine
  -> selected broker adapter
```

AI discovers **what to watch**. Market data decides **whether to act**. RiskEngine decides **whether action is allowed**. AI never calls a broker, changes risk limits, supplies an arbitrary broker symbol, or bypasses feed health or the kill switch.

## Trust and lifecycle

Article text is untrusted data, even when it contains apparent commands. The system prompt labels it as evidence only and the validator rejects missing fields, invalid scores/enums, unknown instruments, and order/action fields. Successful responses become deterministic-ID `market-thesis-v1` records with catalyst, hashes, provider/model, latency, expiry, reasoning, warnings, invalidation conditions, and affected assets. Failed analysis remains an auditable unavailable AI record and ingestion continues.

Affected assets can be `WATCHING`, `CONFIRMED`, `REJECTED`, `EXPIRED`, or `RESEARCH_ONLY`. Unavailable and non-shortable instruments are research-only. Focus is capped and deterministically prioritized. A reference strategy signal on a completed one-minute candle must align with thesis direction and minimum strength; stale moves, opposite direction, unhealthy feeds, low confidence, expiry, RiskEngine rejection, Stop Trading, and emergency stop all produce zero execution.

## Configuration and operation

Set `[ai].decision_mode = "news_thesis"`. Configure RSS/Atom URLs under `[news].feed_urls`; polling is asynchronous, bounded, UTC-normalized, deduplicated, and failure tolerant. RSS fetches feeds only—it does not scrape linked pages.

Manual injection uses the same normalization, AI queue, thesis validator, persistence, and focus path:

```bash
python3 -m feline news inject --headline "Major export disruption reported" --body "Supply routes are affected" --source manual
```

For an offline deterministic smoke test, `--response-fixture tests/fixtures/news_impact_bullish.json` replaces only the model transport; normalization, validation, persistence, focus, and confirmation remain the production path. Fixture responses are explicitly test-only and cannot place orders because injection starts a disarmed runtime.

Local llama.cpp is optional and repository-local by default. Install the pinned runtime/default Qwen model explicitly, then manage only Feline's process:

```bash
python3 -m feline ai install --yes
python3 -m feline ai start-local
python3 -m feline ai status
python3 -m feline ai stop-local
```

Feline records and stops only the process it launched. An externally managed OpenAI-compatible endpoint remains supported. GGUF files and symlinks live under ignored `models/`; see [Portable Local AI](LOCAL_AI.md) for model selection, custom GGUF, integrity, hardware guidance, and offline behavior.

## Replay and limitations

Mixed JSONL accepts `type: "news"` records. Deterministic tests/research use a fixture AI response or recorded output; live LLM calls are never mandatory for reproducible replay. The initial confirmation plugin deliberately reuses the frozen reference SMA/momentum signal rather than introducing a new optimized strategy. RSS supplies headline/summary metadata only. Provider availability, model interpretation, and historical observations do not establish profitability.

## v0.17.4 schema boundary

The full-Qwen v0.17.3 benchmark exposed a prompt/validator mismatch: the prompt named nested `confidence`, `relevance`, and `monitoring_priority` but did not state that all were numeric `[0,1]` scores. Qwen commonly returned priority ranks (`2`, `3`) or labels (`high`, `medium`, `low`). One irrelevant case invented `UNKNOWN` rather than using an empty list.

The contract now explicitly permits `affected_instruments: []` for no defensible material impact and requires exact universe IDs, numeric bounded scores, fixed direction enums, string arrays, and no action/order fields. Managed-local generation is constrained by the pinned llama.cpp JSON Schema facility, but validation remains mandatory and was not relaxed. News text—including embedded JSON, system-like labels, and trading commands—remains untrusted evidence.
