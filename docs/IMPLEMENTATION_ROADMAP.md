# Implementation Roadmap

## Phase 0 — Specification

Status: this blueprint.

## Phase 1A — Local Engine Foundation

Status: complete on 2026-08-27.

- install/pin NautilusTrader;
- create project skeleton;
- implement config;
- prove paper/sandbox runtime;
- implement logging.

Exit: clean reproducible startup.

Verified evidence:

- `setup.bat` installs pinned NautilusTrader and developer checks into `.venv`;
- `check.bat` passes 10 unit/integration tests, Ruff, and mypy;
- `start_bot.bat` starts NautilusTrader 1.231.0 in simulation, initializes 300 USDT at 1x,
  processes a synthetic perpetual quote, verifies the RiskEngine is active, writes a rotated JSON
  log, and exits cleanly;
- unsafe mode, live-enable, and live-confirmation values fail closed;
- no live execution adapter, order-submission path, strategy, Gate market-data adapter, or dashboard
  is implemented.

## Phase 1B — Real Market Data

Status: partial. Public Gate futures REST screening, stale-data detection and explicit recovery are
implemented. A separate public Gate WebSocket capture command now provides subscriptions,
normalization, bounded backpressure, REST snapshot/sequence synchronization, reconnects, atomic
manifests and deterministic hash-chain verification. Full local depth reconstruction and a
long-running capture acceptance run remain pending.

- Gate.io public market data;
- 5–10 candidate perpetual pairs;
- L2/trade ingestion;
- reconnect;
- integrity checks;
- local capture.

Exit: stable long-running feed.

## Phase 1C — Replay and Simulator

Status: partial. Nautilus streaming paper execution now covers public best-bid/ask market fills,
taker fees, configured adverse slippage, 1x position accounting and signal/fill audit. Historical
raw-event integrity replay is available, but same-code strategy replay, partial fills, measured
execution latency, funding and queue-aware maker fills remain pending.

- replay captured data;
- realistic costs;
- partial fills;
- latency model.

Exit: deterministic repeatable research runs.

## Phase 1D — Strategy Lab

Status: partial. An eight-symbol REST-momentum tournament is armed for forward paper observation.
Every screened pair is monitored, while one shared execution slot selects only the highest-confidence
eligible signal. It is cost-gated and risk-capped but has not passed backtest, out-of-sample or
walk-forward acceptance.

- order-flow challenger;
- mean-reversion challenger;
- micro-maker challenger;
- independent statistics.

Exit: enough data to reject/retain candidates.

## Phase 1E — Risk and Operations

- hard limits;
- halt states;
- watchdog;
- restart/recovery;
- storage/log rotation.

Exit: fault-injection tests pass.

## Phase 1F — Dashboard

Status: operations-console MVP implemented, including an authoritative open-trade price ticker and
rolling 48-hour closed-trade audit. Full research/settings pages remain pending their backend data from
Phases 1B–1E.

- overview;
- markets;
- strategies;
- trades;
- research;
- health;
- safe settings.

Exit: Playwright critical path passes.

## Phase 1G — Long Paper Validation

- run on laptop;
- collect thousands of signals/trades where market opportunities permit;
- compare modeled vs observed fills;
- tune only where justified by data.

Exit: evidence-based go/no-go decision.

## Future Phase 2

Not part of current implementation:
- Gate.io TestNet execution;
- private API state reconciliation;
- live-order adapter;
- small-capital live validation;
- higher leverage only after validated risk analysis;
- VPS migration.
