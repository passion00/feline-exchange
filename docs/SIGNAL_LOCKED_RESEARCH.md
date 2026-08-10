# Signal-locked strategy research

When a dataset quality sidecar exists, signal research refuses `REJECTED`
data. Provider normalization does not alter canonical entry, exit, overlap, R,
or friction-overlay semantics.

Feline has two deliberately different research layers. `research continuous run` is a path-dependent paper portfolio: positions, risk, protective fills, costs and equity can suppress or change later activity. `research signals run` is predictive-signal research: it records every eligible opportunity before portfolio state, resolves one immutable reference outcome, and overlays friction without changing that opportunity.

## Timing and anti-lookahead

An opportunity is created only after its 1-minute candle is complete. The established decision-time close is the reference entry and is known at `signal_available_at`; the signal candle's already-known range is never used for exit. Starting with the next completed candle, long/short stops use OHLC. If its open gaps beyond the stop, the open is used and loss may exceed -1R. Otherwise the stop price has precedence. There is currently no reference target in the frozen continuous strategies. Remaining trades exit at the fifteenth subsequent completed-candle close. Near-dataset-end opportunities without the full outcome window remain in the opportunity ledger but have no canonical trade.

Predictors and outcomes are separate JSONL artifacts. Opportunity predictor dictionaries contain no future labels. Deterministic IDs bind dataset checksum, instrument, timestamp, strategy, direction, configuration and model versions.

## Views and R

`all_opportunities` permits overlapping reference trades because it studies each signal independently. `non_overlapping_reference` accepts the first trade per instrument+strategy and suppresses only until that canonical exit; costs, fills, equity and other strategies cannot change it.

`initial_unit_risk = abs(reference_entry - reference_stop) × contract_multiplier`. `reference_gross_R = directional reference price movement × contract_multiplier / initial_unit_risk`. Display USD uses a fixed, non-compounding `starting_equity × risk_fraction` (USD 250 by default).

## Friction overlays

Research-default spread and adverse slippage are attributed from the v0.11.2 uncalibrated execution profile. Multipliers apply consistently to spread, slippage, commissions and financing. The identity is `gross - spread - slippage - commission - financing = hypothetical net`; 0× equals gross. Overlays are not real execution.

Reports include all strategies (including zero samples), exact cost scenarios, break-even friction, predictor-only subgroup bins, tail concentration, daily stability and deterministic UTC-day block bootstrap intervals. These are inspected development data and exploratory descriptive statistics—not threshold optimization, a portfolio backtest, broker calibration, holdout validation, or evidence of live/future profitability.
