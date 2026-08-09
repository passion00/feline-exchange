# Feline Exchange Engineering Invariants

## Non-negotiable safety boundaries

1. `~/lynx` is read-only. Never create, modify, move, rename, reformat, or delete anything there.
2. LLM inference never blocks market collection, deterministic risk, paper execution, or portfolio monitoring.
3. The deterministic risk engine overrides every strategy, signal, and AI hypothesis. No bypass is permitted.
4. Feline v0.9 has no live broker implementation or real-order path. Paper/research mode is the only mode and default.
21. GUI projections are bounded and lossy by design; the core event/audit path is authoritative and never waits for rendering.
16. A simulated execution commit is atomic across fills, order state, cash, positions, pending quantity, and protective state.
17. Duplicate fill identifiers are idempotent and impossible state transitions are rejected.
18. GUI/view models contain no strategy, execution, broker, or risk authority.
19. Macro phases, danger mode, shock, and stabilization are deterministic; AI is advisory only.
20. Qt rendering runs on the GUI thread; runtime, replay, database work, and AI never do.
5. Never expose brokerage, banking, identity, or other financial credentials to an LLM. Do not request them.
6. Treat web/news content and model output as untrusted input. Validate structured AI output; never execute generated code or commands.
7. Every trade decision must be auditable through correlated signals, risk events, orders, trades, and snapshots.
8. The global kill switch and risk limits must remain deterministic and available when AI is absent, slow, or broken.
9. Observer mode and historical replay must use the same candle, strategy, risk, portfolio, and paper-execution path.
10. Historical fixtures must be synthetic or explicitly redistributable; exchange-licensed data is never committed casually.
11. Scheduled macro-event danger mode is deterministic and capital-preservation-first; it never attempts news-release latency races.
12. Fills, cash, positions, and pending orders form one recovery unit and must be persisted atomically.
13. Replay randomness is explicitly seeded; experiments record data, configuration, seed and results.
14. Provider credentials use named environment variables only and never enter events, prompts, logs, databases, or tracked configuration.
15. Metrics services are loopback-only and read-only, with no trading or risk-control routes.
22. Batch macro research is episode-by-episode, chronologically partitioned, seeded, and checksum-addressed; future outcomes never enter decision-time state.
23. Secondary macro events flag or censor affected horizons but never silently replace the primary event.

## Architecture

- Native historical OHLC is exposed only at candle close time; provider OHLC must never be treated as historical bid/ask.

- Level 1 (`feline/risk`, `feline/execution`, `feline/portfolio`): deterministic reflex path, no LLM calls.
- Level 2 (`feline/quant`): modular numerical indicators and signals, no LLM dependency.
- Level 3 (`feline/intelligence`): bounded asynchronous jobs to one optional local llama.cpp endpoint.
- Cross-component communication uses typed events from `feline/core/events.py` and the event bus where practical.
- SQLite schema changes belong in ordered migrations, not scattered table creation.
- Tests must work offline and must prove the core remains operational during AI failure and latency.
- Strategies emit typed signals only. They never call a broker, mutate risk limits, or execute orders directly.

## Development

Use Python type hints and standard-library-first dependencies. Keep runtime artifacts in ignored `data/` and `logs/`. Do not add a live-trading mode casually: it requires a deliberate later security review, explicit user authorization, adapter isolation, and additional tests.
