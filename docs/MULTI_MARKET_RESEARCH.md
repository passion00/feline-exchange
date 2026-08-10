# Multi-market continuous research

Feline v0.11.2 compares frozen continuous reference hypotheses across EURUSD, XAUUSD, and BTCUSD without ranking markets by nominal P/L. Market profiles define units and calendars; execution profiles define synthetic friction independently. Every profile and sizing input is recorded in `experiment.json`.

Use `--execution-profile reference_zero_cost` only to inspect pre-friction directional behavior. Use `research_default` for the explicit uncalibrated assumptions documented in `CONTINUOUS_RESEARCH.md`. Risk-normalized runs use current equity, a 0.25% default risk budget, the unchanged normalized 10-bp stop, contract multiplier, and deterministic exposure caps.

Compare completed reports with:

```bash
python3 -m feline research continuous compare REPORT_A REPORT_B REPORT_C
python3 -m feline research continuous compare REPORT_A REPORT_B REPORT_C --comparison-basis common
```

Native mode uses each experiment's full market time. Common mode reports the completed timestamps shared by every input as descriptive overlap metadata; it does not retroactively change signals or trades. Reports include every strategy family, including zero-trade and losing families.

Provider files remain local/ignored. Local Twelve Data conversion accepts normalized `EURUSD`, `XAUUSD`, and `BTCUSD` instrument names and never needs an API key. Provider price OHLC is not bid/ask data; execution spread remains synthetic.
