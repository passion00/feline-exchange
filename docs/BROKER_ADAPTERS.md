# Broker adapters and Broker Manager

Feline v0.16 separates broker connectivity from strategy, AI, risk, and orchestration. `BrokerAdapter` exposes capability discovery, connection, quote streaming, historical availability, instrument discovery, account state, positions, orders, fills, cancellation/modification, and reconciliation. Unsupported operations raise `UnsupportedBrokerCapability`; generic code never assumes OANDA behavior.

## Broker Manager workflow

1. Run `python3 -m feline gui` and open **Broker Manager**.
2. Create an OANDA profile. Profiles default to `practice` and store only name, adapter, environment, account identifier, credential environment-variable name, and default instrument.
3. Enter the token in the password field and press **Connect**. The value is placed only in this process environment, immediately cleared from the widget, and never written to the profile, database, event payload, report, or log. Alternatively export `FELINE_OANDA_API_TOKEN` before launch.
4. Connection populates account balance/equity/margin, discovered instruments, live bid/ask/chart, positions, orders, and fills. Trading remains **STOPPED**.
5. Press **Start Trading** to arm the existing strategy → AI → RiskEngine → selected adapter path. Press **Stop Trading** to prevent new strategy orders without weakening synchronization or the kill switch. Disconnect separately in Broker Manager.

OANDA account identifiers are non-secret profile metadata. `data/broker_profiles.json` is under the ignored `data/` tree and is atomically replaced. Credentials must use named environment variables.

## Safety boundary

Internal `PaperBroker` remains the offline/replay/test implementation. OANDA `practice` is external demo execution. Live execution cannot be enabled in the GUI: it requires a profile deliberately created with `live_execution_enabled=true` **and** the process variable:

```bash
export FELINE_ENABLE_LIVE_BROKER=YES_I_ACCEPT_LIVE_RISK
```

Both gates are required. A practice profile cannot become live accidentally. AI cannot originate orders, and every strategy order still passes feed health, autonomous Start/Stop, deterministic RiskEngine, exposure limits, stops, and kill switch.

## OANDA adapter

The first external adapter supports OANDA practice authentication, pricing stream, native historical candles, account summary, instrument discovery, positions/pending-order synchronization, market/limit/stop requests, cancel and cancel-replace, and immediate market-fill acknowledgements. Request IDs are sent as client extensions and duplicate local request IDs are blocked. Reconnect reconciliation compares local and broker positions and surfaces disagreement. OANDA's separate transaction stream is not yet consumed, so the adapter truthfully advertises asynchronous `execution_updates=false`; pending-order fills are reconciled rather than presented as a streaming capability.

Audit tables contain non-secret profiles, broker sessions, complete normalized order requests, broker order IDs, acknowledgements, cancellations, rejects, immediate fills, and reconciliation outcomes. Duplicate event/fill identifiers are idempotent. OANDA client request IDs are reconciled from the latest 500 broker orders on connection; operators should treat a reconciliation disagreement as a stop condition and inspect the broker account before arming. The adapter refreshes account/order/position state periodically while streaming.

## Practice verification

Set `FELINE_OANDA_API_TOKEN` and `FELINE_OANDA_ACCOUNT_ID`, then launch `python3 -m feline gui`. Create/select a **practice** profile, connect, verify account and quotes, and press Start Trading. No order is forced: the frozen strategy must produce a valid signal and pass AI/risk. Observe or cancel any practice order in both Feline and the OANDA practice UI, confirm matching IDs, then Stop Trading and Disconnect. For a bounded feed-only check, run:

```bash
python3 -m feline realtime start --environment practice --instrument EURUSD --duration 60
```

This release does not change strategy, AI, or risk thresholds and makes no profitability claim.
