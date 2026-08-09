# Execution and research methodology

Fills are deterministic for a configuration, dataset, and seed. Buy reference price is ask; sell reference price is bid. Directional slippage combines configured base, spread, size, and regime terms. Available quantity is `tick volume × liquidity_fraction`; zero/missing volume means unlimited synthetic liquidity. Each partial fill independently updates cash, costs, quantity, and weighted entry.

Latency runs from acceptance to the executing quote while replay continues normally. Limit/stop conditions are evaluated after latency eligibility. Gaps execute at observed bid/ask plus slippage, never at an unavailable trigger price.

Atomic recovery stores cash, positions, pending requests, remaining quantities, and protective prices in one SQLite `BEGIN IMMEDIATE` transaction. Fill and financing ledgers remain auditable.

v0.4 commits the fill ledger, order update, cash/broker payload, positions, pending quantities, and protective state in the same transaction. Stable fill primary keys make retry-after-commit idempotent. Fault-injection hooks verify rollback before commit and exactly-once visibility after commit. Order transitions are explicitly validated.

Experiments stream SHA-256 dataset checksums and record Feline/Git version plus repository dirty state. Synthetic instrument profiles are research defaults, never broker specifications.

Parameter grids are bounded (library maximum 64; CLI default 16). Walk-forward training ends strictly before validation starts. Current selection is intentionally skeletal and makes no statistical-validity claim. Account for multiple testing, leakage, selection/survivorship bias, nonstationarity, and model error.

Synthetic stress scenarios include five-percent FX moves, spread widening, liquidity collapse, gaps, and correlated movement. Seeded trade resampling explores approximate drawdown, losing streaks, and ruin frequency; it cannot provide guarantees.
