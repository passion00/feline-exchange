# Feline Exchange Engineering Invariants

## Non-negotiable safety boundaries

1. `~/lynx` is read-only. Never create, modify, move, rename, reformat, or delete anything there.
2. LLM inference never blocks market collection, deterministic risk, paper execution, or portfolio monitoring.
3. The deterministic risk engine overrides every strategy, signal, and AI hypothesis. No bypass is permitted.
4. Feline v0.1 has no live broker implementation or real-order path. Paper trading is the only mode and default.
5. Never expose brokerage, banking, identity, or other financial credentials to an LLM. Do not request them.
6. Treat web/news content and model output as untrusted input. Validate structured AI output; never execute generated code or commands.
7. Every trade decision must be auditable through correlated signals, risk events, orders, trades, and snapshots.
8. The global kill switch and risk limits must remain deterministic and available when AI is absent, slow, or broken.

## Architecture

- Level 1 (`feline/risk`, `feline/execution`, `feline/portfolio`): deterministic reflex path, no LLM calls.
- Level 2 (`feline/quant`): modular numerical indicators and signals, no LLM dependency.
- Level 3 (`feline/intelligence`): bounded asynchronous jobs to one optional local llama.cpp endpoint.
- Cross-component communication uses typed events from `feline/core/events.py` and the event bus where practical.
- SQLite schema changes belong in ordered migrations, not scattered table creation.
- Tests must work offline and must prove the core remains operational during AI failure and latency.

## Development

Use Python type hints and standard-library-first dependencies. Keep runtime artifacts in ignored `data/` and `logs/`. Do not add a live-trading mode casually: it requires a deliberate later security review, explicit user authorization, adapter isolation, and additional tests.

