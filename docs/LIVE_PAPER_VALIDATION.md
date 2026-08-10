# Live Paper Validation and AI Evaluation

Feline v0.15 wraps the existing OANDA-practice-to-PaperBroker path in an auditable validation session. It does not submit live orders and does not change strategy, prompt, risk, or execution thresholds.

## Controlled modes

- `deterministic`: AI is disabled and every deterministic signal proceeds to the existing risk gate.
- `advisory`: the same deterministic path executes while structured AI assessments are recorded (`record` internally).
- `confirm_or_veto`: a deterministic signal proceeds only after a timely, sufficiently confident, directionally consistent AI response. AI still cannot originate an order.

Run bounded OANDA practice sessions with the same committed configuration, instrument, duration, and UTC schedule:

```bash
python3 -m feline --config config/feline.toml realtime start --environment practice --instrument EURUSD --duration 3600 --validation-mode deterministic
python3 -m feline --config config/feline.toml realtime start --environment practice --instrument EURUSD --duration 3600 --validation-mode advisory
python3 -m feline --config config/feline.toml realtime start --environment practice --instrument EURUSD --duration 3600 --validation-mode confirm_or_veto
```

Each run starts a fresh paper portfolio and writes `summary.json`, `summary.csv`, and `summary.md` under `data/reports/realtime_validation/<session-id>/`. The SQLite `realtime_validation_summaries` row holds the same immutable machine-readable summary. OANDA and model credentials remain environment-only.

Inspect or compare exports:

```bash
python3 -m feline validation inspect data/reports/realtime_validation/SESSION
python3 -m feline validation compare SESSION_DETERMINISTIC SESSION_ADVISORY SESSION_CONFIRM --output data/reports/realtime_validation/comparison
```

## Metrics and automatic status

Summaries include source period and configuration checksum; quote/feed states; deterministic and final signals; AI latency and confidence distributions; timeout/error, stale, approval and veto rates; blocked reasons; risk rejections; orders, fills and attributed execution costs; ending equity, realized/net P&L, lifecycle trade count, win rate, profit factor, realized-equity drawdown, MAE/MFE, and exposure time.

`FAIL` means no valid market quotes. `WARN` identifies absent signals/AI assessments, observed feed degradation, incomplete lifecycle coverage, or fewer than 30 completed trades. `PASS` means the mechanical validation gates passed; it is not evidence of profitability or statistical significance.

Comparison is intentionally strict. It declares an AI effect only when modes cover the exact same first/last source timestamp interval and instruments, configuration differences are explicit, and every session has at least 30 completed trades. Sequential live sessions normally fail the exact-period test and therefore report `insufficient_evidence`. Use them to validate operations, latency, and failure rates—not causal performance. A later frozen same-tick replay/shadow experiment can supply exact-period evidence without retuning on these samples.

MAE and MFE follow the existing realtime lifecycle representation. The summary reports lifecycle coverage separately from broker-authoritative equity, realized P&L, fills, and costs, so incomplete closed-trade coverage cannot silently masquerade as complete performance evidence.

No automatic prompt or threshold optimization is performed. Results are descriptive paper-trading measurements only.
