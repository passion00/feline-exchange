# v0.11.4 replication protocol

February 5–March 2, 2024 is inspected development data used for a provider
cross-check: Twelve versus Dukascopy for EURUSD/XAUUSD, and Twelve BTCUSD
versus Binance Spot BTCUSDT (provider, venue, and symbol sensitivity—not an
identical-series comparison).

The frozen replication interval is `[2024-04-01T00:00:00Z,
2024-10-01T00:00:00Z)`. Quality must pass before outcomes are opened. Frozen
v0.11.3 settings are strategy `all`, equity USD 100,000, risk fraction 0.0025,
uncalibrated `research_default`, multipliers 0/0.25/0.5/0.75/1/1.5/2, seed 17.

Predeclared hypotheses are EURUSD `range_mean_reversion` gross expectancy R >
0, and BTCUSDT `trend_pullback` gross expectancy R > 0 with explicit tail
concentration analysis. Fixed verdicts are `REPLICATED_POSITIVE`,
`POSITIVE_BUT_UNCERTAIN`, `FAILED_REPLICATION`, or `INSUFFICIENT_DATA`.
Everything else is exploratory. Positive gross signal and survival of
synthetic friction are separate questions; this is not a profitability claim.
