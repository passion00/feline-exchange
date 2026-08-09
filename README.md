# Feline Exchange v0.1 — Observer + Paper Trader

Feline Exchange is a local-first market-observation and quantitative paper-trading foundation. Version 0.1 cannot submit real orders: the only broker is an in-memory `PaperBroker`, and configuration rejects every mode except `paper`.

## Three-speed architecture

```text
Mock/external providers -> typed events -> Level 1 reflex/risk -> PaperBroker -> SQLite audit
                                     |-> Level 2 modular indicators/signals
                                     `-> bounded AI queue -> Level 3 llama.cpp analysis -> validated result
```

Level 1 never calls or waits for an LLM. Level 2 performs ordinary numerical work. Level 3 is optional, slow, bounded, and failure-tolerant. A missing or hung local model produces an unavailable analysis while the market loop continues.

## Start

Python 3.11+ is required; v0.1 has no third-party runtime dependencies.

```bash
cp config/feline.example.toml config/feline.toml
python3 -m feline status
python3 -m feline paper
```

For a short smoke run:

```bash
python3 -m feline paper --duration 3
```

Install an optional CLI entry point with `python3 -m pip install -e .`, then use `feline paper`. AI defaults to Lynx's local llama.cpp-compatible endpoint at `127.0.0.1:8081`, but Feline does not start or require that server.

Emergency stop creates an ignored persistent marker, is detected by a running market loop on its next tick, and blocks later starts:

```bash
python3 -m feline stop
```

Remove `data/EMERGENCY_STOP` only after deliberate operator review to re-enable startup. In-process limits can also activate the risk engine's global kill switch.

## Packages

- `core`: immutable typed events and async publish/subscribe
- `market`: provider interfaces and offline mock ticks
- `quant`: rolling returns, volatility, moving average, RSI
- `risk`: deterministic position, exposure, loss, drawdown, spread, volatility, and kill-switch checks
- `execution`: broker interface and local paper fills
- `portfolio`: positions and realized P/L accounting
- `intelligence`: bounded jobs, local llama.cpp client, strict output validation
- `storage`: WAL-mode SQLite and ordered schema migrations

The schema stores market/news events, analyses, signals, paper orders/trades, positions, snapshots, risk events, and system events. IDs and correlation fields make decisions reconstructable; future strategy work should consistently carry a correlation ID from source event through signal, risk decision, and order.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Tests are offline and include a deliberately slow AI client while verifying ticks continue to persist.

## Lynx adaptation notes

Feline adapts Lynx's useful patterns rather than copying its project: local OpenAI-compatible llama.cpp HTTP integration, loopback defaults, dataclass configuration, parameterized SQLite, and local/offline operation. It intentionally does not reuse Lynx's conversation memory, summarization, fact extraction, Wikipedia, filesystem tools, permission router, TTS, chat orchestration, or large PySide6 GUI. Lynx's unbounded model request and blocking server startup are unsuitable for the real-time path; Feline instead uses a timeout-bounded background queue and starts safely without a model.

No GUI is included yet. A future dashboard should consume events/read models and remain outside the risk/execution authority path.
