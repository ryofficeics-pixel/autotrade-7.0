# Architecture

## Design Rule

One execution authority only: NautilusTrader.

Hummingbot is a source of strategy patterns and crypto-specific ideas. Do not run Hummingbot and NautilusTrader as competing order owners.

## Logical Architecture

```text
Gate.io WebSocket / Public REST
            |
            v
+-----------------------------+
| Market Data Adapter         |
| timestamps / normalization  |
+-----------------------------+
            |
            v
+-----------------------------+
| NautilusTrader              |
| event bus / cache / book    |
| portfolio / risk / orders   |
+-----------------------------+
        |            |
        |            +------------------+
        v                               |
+----------------+                     |
| Strategy Lab   |                     |
| - order flow   |                     |
| - mean rev     |                     |
| - micro maker  |                     |
+----------------+                     |
        |                               |
        v                               v
+----------------+              +------------------+
| Strategy       |------------->| Risk Gate        |
| Selector       |              +------------------+
+----------------+                       |
                                         v
                               +--------------------+
                               | Paper Execution    |
                               | Simulator          |
                               +--------------------+
                                         |
                                         v
                                Portfolio / Metrics
                                         |
                           +-------------+-------------+
                           |                           |
                           v                           v
                    Local persistence             API server
                                                       |
                                                       v
                                                Web dashboard
```

## Architectural Boundaries

### Optional TradingView Research Sidecar

```text
TradingView Desktop -> localhost CDP -> pinned tv CLI -> background health monitor -> API/UI
```

This path is observational and separately healthy/unhealthy. It has no connection to strategy input,
Risk Gate, sizing, order submission, positions or portfolio authority. The integration reads
`tv status`, `tv quote`, and `tv values` only for the dashboard; upstream terms prohibit using the
extracted data for automated trading decisions.
See `TRADINGVIEW_INTEGRATION.md`.

### Trading Core

Owns:
- market state;
- strategy state;
- positions;
- orders;
- risk;
- PnL;
- execution simulation.

Must not depend on the dashboard being open.

The Phase 1 REST pilot is implemented as a distinct `PaperTrader` object owned by the long-running
local backend process. One Nautilus account hosts the screened per-symbol observers and a single
portfolio-wide execution slot, preserving one total open position. `PaperTrader` owns strategy,
selection, order, position, risk and PnL state. HTTP handlers only read snapshots or request
pause/resume/flatten controls; closing the browser does not stop it.

Paper experiment evidence is segmented under `data/runs/<run_id>/`: immutable metadata and a schema-v3
JSONL ledger. The recoverable checkpoint remains under `logs/paper-state.json` and is written by
fsync plus atomic replacement. Startup reconciliation is the gate between persisted state and the
Nautilus simulated account; ambiguity becomes `STATE_INVALID`, never a configured-balance reset.

Quote construction is symbol-scoped. `PaperTrader` can quarantine one malformed contract while the
shared account, risk engine, and remaining instrument strategies continue. Global halt remains reserved
for account, risk, persistence, execution-state, or authoritative feed integrity failures.

### Dashboard/API

Read/control plane only.

Allowed:
- start/pause paper strategy;
- display system health;
- view PnL, fills and logs;
- configure allowed Phase 1 parameters.

Not allowed:
- calculate authoritative position state;
- independently infer fills;
- bypass Risk Gate;
- submit live orders.

## Technology Direction

Prefer:
- Python 3.12+ compatible with chosen Nautilus stable release;
- NautilusTrader stable release, pinned;
- FastAPI or equivalent minimal API only if needed;
- React/Next.js only if the dashboard needs richer interaction than server-rendered/native HTML;
- SQLite initially unless write throughput proves inadequate;
- Parquet for historical market-data archive;
- Playwright for end-to-end UI audit.

Do not introduce Redis, message brokers, Kubernetes, microservices or cloud infrastructure during Phase 1 unless a measured bottleneck demands them.

## Nautilus Alignment

NautilusTrader provides an event-driven architecture spanning research, deterministic simulation, portfolio/risk modeling and live execution. Strategies are intended to move between research and live contexts without being rewritten.

Reference:
https://nautilustrader.io/docs/latest/concepts/
