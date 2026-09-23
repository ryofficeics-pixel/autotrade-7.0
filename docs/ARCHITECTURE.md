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

### Market Scope and Single Entry Authority

`MarketScopeController` sits between market-profile signal routing and `PaperTrader`'s final order
submission. It is the only durable source for `WIDE_CRYPTO` versus `XAU_ONLY` and for the explicit
switch state machine: `ACTIVE`, `SWITCH_REQUESTED`, `DRAINING`, `FLATTENING`, `RECONCILING`,
`ACTIVATING`, or `FAILED`.

```text
Gate public metadata and quotes
             |
      MarketScopeController
        /              \
WIDE_CRYPTO          XAU_ONLY
scanner/ranking      XAU signal + XAUT/PAXG confirmation
        \              /
          final entry guard
                 |
      shared Nautilus PAPER engine
      risk / positions / ledger / recovery
```

The final entry guard immediately precedes `submit_candidate`. It validates PAPER permission, active
scope, transition state, allowed instrument, freshness, Gate contract metadata, spread, confidence,
risk state, duplicate-position state, and cooldown-controlled strategy eligibility. A profile cannot
submit independently. The dashboard remains a read/control plane.

The XAU profile fails closed when `XAU_USDT` is missing, disabled, delisting, stale, or incompatible
with configured 1x leverage and discovered price/quantity rules. It never falls back to XAUT, PAXG,
XAU5L, XAU5S, or a crypto symbol. One stale optional confirmation degrades the confirmation and size;
it does not crash the execution feed.

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

`POST /api/control/analyze-repair` runs a bounded, deterministic health check in the
backend. It serializes with the existing feed poller and paper state lock, refreshes public
quotes, revalidates the durable checkpoint against every attributed fill, and resumes only
when the existing safety gates pass. No AI service, shell command, dependency, or browser
execution logic is involved. Concurrent repair requests are rejected; results are returned
in `/api/state` and recorded in the dashboard log.

Open-position reconciliation aggregates all entry fills, including fills after the first
`position_opened` event. It verifies identity, quantity, weighted price, and entry fees;
it never rewrites the ledger or resets the paper account. An incomplete exit remains unsafe.

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

### Offline Research and Promotion Boundary

The offline research process consumes a verified prefix of the runtime AMA evidence and writes only
under `research/` and `reports/`. It has no reference to `PaperTrader`, the control endpoints, market
scope, or Nautilus order submission. Dataset and experiment identity bind source prefix, content
hashes, code commit, research configuration, strategy/parameter hashes, execution-profile version,
and deterministic seed.

Candidate generation, counterfactual resolution, cost decomposition, chronological validation,
holdout sealing, registry, and lifecycle state are shared across reconstructable challengers.
Runtime REST Momentum and Entry V3 are reported as non-reconstructable when required inputs are not
on the frozen timeline. The dashboard loads a bounded read-only summary; closing it cannot affect
research or trading. See `RESEARCH_ENGINE.md`.

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

### Independent XAU KAMA Control

`AmaControlEngine` consumes the same REST ticker poll as the running PAPER engine but has no reference
to Nautilus order submission. It runs continuously in both market scopes, calculates KAMA 10/20/50,
classifies regime, evaluates a raw KAMA20 cross and a quality-gated variant, and records hypothetical
shadow positions. `xau.ama_control.execution_enabled` is required to remain false at configuration
load and again at engine construction.

The quality gate is centralized and returns deterministic allow/reject codes. Reference evaluation
uses `XAUT_USDT` and `PAXG_USDT` as research inputs, reports dispersion and XAU dislocation, and fails
the filtered shadow candidate closed when configured reference requirements are not met or prices are
abnormal. It never replaces `XAU_USDT` for execution.

Research records are hash-chained under `data/runs/<run_id>/ama-control-v2.jsonl`. V2 enforces the
documented price-plus-KAMA20-slope raw candidate rule; any pre-validation V1 evidence remains separate
and is not used in V2 metrics. Restart verifies the
entire chain and reconstructs rolling KAMA state, open shadow positions, closed shadow trades, and
unresolved recent counterfactual horizons. A research failure is surfaced as an AMA error and cannot
authorize or submit an order.
