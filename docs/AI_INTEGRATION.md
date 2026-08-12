# AI Integration

## v0.17.6 model selection

Qwen3 4B Q4_K_M remains control/default; Qwen3.5 4B Q5_K_M is experimental. Both receive the same universe, schema, validator, seed, sampling, reasoning, deadline and execution-disabled experiment path. A newer model receives no additional authority.

## v0.17.5 purpose-specific reasoning

News market-impact jobs default to Qwen thinking mode, a 900-second total deadline, and explicit sampling (`temperature=0.6`, `top_p=0.95`, `top_k=20`, `min_p=0`, seed 17). Short-lived signal assessment remains non-thinking with a 30-second deadline. Managed llama.cpp receives thinking and seed controls per request; external endpoints receive only portable parameters. Hidden `reasoning_content` is discarded. Only strict final JSON may construct a thesis. A structured causal effect must agree with directional bias; this check does not infer sentiment from prose or grant execution authority.

The explicit seed reaches llama.cpp sampling, improving controlled comparisons. Byte-identical output is not guaranteed across runtime builds, hardware scheduling, or external providers.

Feline v0.17 adds the dedicated `news_thesis` purpose: untrusted news is interpreted into a bounded, structured market hypothesis over Feline's supplied instrument universe. Price action—not the model—must subsequently produce deterministic confirmation. The older `advisory`, `record`, and `confirm_or_veto` modes remain compatible; they are not the only intended role of AI. See [News Intelligence](NEWS_INTELLIGENCE.md).

Feline v0.17.1 added a portable managed-local mode. Feline can explicitly install a pinned llama.cpp runtime and selected verified GGUF under repository-local ignored directories, while externally managed OpenAI-compatible endpoints remain supported. Startup checks assets but never silently downloads them. See [Portable Local AI](LOCAL_AI.md).

Feline v0.17.2 adds fixture, managed-local, and external-provider benchmarks over that same production path. Safety invariants and semantic usefulness are deliberately reported separately; see [Experiments](EXPERIMENTS.md).

## Purpose-specific deadlines

Feline v0.17.3 separates AI transport deadlines by job purpose. `analyze_news_for_market_impact` defaults to `news_thesis_timeout_seconds = 300`, intentionally allowing Qwen3 4B CPU inference up to five minutes. A locked deterministic signal remains short-lived and uses `trading_assessment_timeout_seconds = 30`. `request_timeout_seconds` remains the backward-compatible deadline for uncategorized/legacy jobs. Purpose-specific settings take precedence for their respective jobs, and the same selected deadline bounds both HTTP transport and the worker's total retry window.

The 20-second `context_max_age_seconds` and `maximum_price_move_fraction` apply only to old confirm/veto assessment of an already-existing signal. They never reject a news thesis merely because inference took longer. Feline is not an HFT system.

Feline v0.14 introduced a bounded reasoning layer; it does not add a trading authority. Feline v0.15 adds session-level measurement described in [Live Paper Validation](LIVE_PAPER_VALIDATION.md). The OpenAI-compatible provider can point at a local `llama.cpp` server or another compatible endpoint by configuration. No model credentials are included in prompts, events, logs, or the database.

## Decision contract

The provider receives a versioned JSON context containing the instrument and bid/ask state, recent completed candles, deterministic indicators and signal, regime and volatility, positions and exposure, recent signals, normalized macro/news headlines, and realtime feed health. Candle data are included only after completion. News remains untrusted data in the prompt.

The response must validate against `trading-assessment-v1`: instrument, event type, direction, importance, confidence, horizon, summary, reasoning summary, event relevance, risk warnings, suggested action (`LONG`, `SHORT`, `HOLD`, or `NO_TRADE`), and evidence. Scores must be in `[0, 1]`; the instrument must match the context. Malformed responses become unavailable `NO_TRADE` assessments.

`decision_mode = "advisory"` is the backward-compatible replay default. `record` records market-signal assessments without changing execution. Realtime paper mode uses `confirm_or_veto`: an existing deterministic signal is held while AI runs asynchronously. Only a timely, sufficiently confident assessment in the same direction releases it. Unavailable, timed-out, malformed, low-confidence, expired, materially price-moved, or contradictory results veto that signal. AI never creates an order. A released signal still traverses feed-health gating, RiskEngine, exposure and kill-switch checks, PaperBroker, stops, and persistence.

The queue is bounded and ingestion never waits for it. Queue rejection fails closed for that signal. Context expires after `context_max_age_seconds`; a response is also rejected when current mid-price differs from the locked signal reference by more than `maximum_price_move_fraction`.

## Configuration and operation

The `[ai]` section in `config/feline.example.toml` documents provider, endpoint, model, timeout, retry, temperature, token, confidence, freshness, price-movement, decision-mode, and queue settings. Start a compatible local endpoint, copy the example configuration, then run:

```bash
python3 -m feline --config config/feline.toml realtime start --instrument EURUSD
```

Realtime OANDA credentials remain environment-only as documented in `REALTIME_INGESTION.md`. AI is independent of OANDA credentials. If the AI endpoint is absent, realtime collection and monitoring continue while deterministic signals fail safely to `NO_TRADE` in confirm/veto mode.

## Audit and later research

Every assessment persists provider/model identifiers, prompt schema and hash, context hash and timestamp, expiry, structured response, source event IDs, latency, error state, affected deterministic signal, veto flag, and downstream decision. Prompt hashes preserve audit correlation without requiring raw prompt duplication. `advisory` and `record` modes support replay/research collection for later comparison; v0.14 does not optimize prompts, train models, or claim that AI adds value.

The workstation AI Opinion panel shows provider/model health, action, confidence, latency, reasoning, veto state, and downstream outcome. It is a read-only projection; the event/database path is authoritative.

## Safety limitations

- AI never submits orders. v0.16 may route an approved deterministic order to internal paper, external practice/demo, or an explicitly double-gated live adapter; RiskEngine remains authoritative in every case.
- AI cannot alter risk thresholds, stops, sizing, exposure limits, feed gates, or the kill switch.
- A model timeout cannot block quote/candle ingestion or GUI updates.
- The supplied OpenAI-compatible default is a configurable research backend, not a reliability or profitability guarantee.

## Structured news output (v0.17.4)

`news-market-impact-v1` is explicit down to every required type, enum, array, and `[0.0, 1.0]` score. Managed-local llama.cpp b9637 receives a per-request `response_format` JSON Schema whose instrument enum is built from Feline's supplied universe. External OpenAI-compatible endpoints retain the portable prompt-only request because support cannot be assumed. Both paths pass through the same authoritative validator afterward.

Benign fencing or prose around one JSON object can be extracted, but Feline never fills missing fields, clamps scores, maps arbitrary directions, substitutes instruments, or accepts broker-action fields. Malformed, ambiguous, or schema-invalid output remains unavailable and cannot create a thesis or order.
