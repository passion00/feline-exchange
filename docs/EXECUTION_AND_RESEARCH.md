# Execution and research methodology

Fills are deterministic for a configuration, dataset, and seed. Buy reference price is ask; sell reference price is bid. Directional slippage combines configured base, spread, size, and regime terms. Available quantity is `tick volume × liquidity_fraction`; zero/missing volume means unlimited synthetic liquidity. Each partial fill independently updates cash, costs, quantity, and weighted entry.

Latency runs from acceptance to the executing quote while replay continues normally. Limit/stop conditions are evaluated after latency eligibility. Gaps execute at observed bid/ask plus slippage, never at an unavailable trigger price.

Atomic recovery stores cash, positions, pending requests, remaining quantities, and protective prices in one SQLite `BEGIN IMMEDIATE` transaction. Fill and financing ledgers remain auditable.

v0.4 commits the fill ledger, order update, cash/broker payload, positions, pending quantities, and protective state in the same transaction. Stable fill primary keys make retry-after-commit idempotent. Fault-injection hooks verify rollback before commit and exactly-once visibility after commit. Order transitions are explicitly validated.

Experiments stream SHA-256 dataset checksums and record Feline/Git version plus repository dirty state. Synthetic instrument profiles are research defaults, never broker specifications.

Parameter grids are bounded (library maximum 64; CLI default 16). Walk-forward training ends strictly before validation starts. Current selection is intentionally skeletal and makes no statistical-validity claim. Account for multiple testing, leakage, selection/survivorship bias, nonstationarity, and model error.

Synthetic stress scenarios include five-percent FX moves, spread widening, liquidity collapse, gaps, and correlated movement. Seeded trade resampling explores approximate drawdown, losing streaks, and ruin frequency; it cannot provide guarantees.

## Macro-event research

The initial hypothesis compares continuation with mean reversion after Fed/ECB-style volatility begins stabilizing. Deterministic shock intensity uses return magnitude, spread, and velocity. Stabilization requires configurable declining intensity and consecutive stable observations. Strategies abstain during shock or excessive spread. Event measurements support configurable 1/5/15/30/60-minute horizons even when no trade occurs. AI classification is optional counterfactual context and never controls danger mode or submits orders.

Mixed JSONL replay stores timestamped price and normalized economic records and is globally ordered before delivery. Synthetic fixtures are not proprietary observations.

## Qt workstation

PySide6 and pyqtgraph are declared project dependencies. Qt owns rendering only; a dedicated controller thread owns an asyncio loop for core/replay tasks. Chart and event buffers are bounded. AI Opinion, strategy signal, and deterministic risk decision are separate surfaces. Emergency stop writes the same persistent core marker after confirmation; it cannot clear or weaken risk. Manual KDE validation: install editable, run `python3 -m feline gui`, open the Fed JSONL fixture, select a slow speed, verify resizing/zoom/pan/pause, then confirm emergency-stop behavior.
