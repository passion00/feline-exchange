# AI Integration

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

- Paper/research execution only; there is no live-order adapter.
- AI cannot alter risk thresholds, stops, sizing, exposure limits, feed gates, or the kill switch.
- A model timeout cannot block quote/candle ingestion or GUI updates.
- The supplied OpenAI-compatible default is a configurable research backend, not a reliability or profitability guarantee.
