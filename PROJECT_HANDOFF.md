# Autotrade 7.0 — Project Handoff and Build Report

## Document control

| Field | Value |
| --- | --- |
| Report snapshot | 2026-08-30 15:39 WIB (UTC+07:00) |
| Workspace | `G:\codex\autotrade 7.0` |
| Git branch | `main` |
| Git revision | None — repository has an unborn `HEAD` and no commits |
| Working tree | Every project file is untracked at this snapshot |
| Operating mode | Phase 1, PAPER ONLY |
| Primary engine | NautilusTrader 1.231.0 |
| Python | 3.12.10, 64-bit Windows |
| Verification | 39/39 Python tests, Ruff, mypy, and 3/3 Playwright tests passed on the current files |

> **Snapshot integrity warning:** this is a working-directory report, not a commit report. A test
> assertion changed from 6 expected fill events to 4 while this report was being prepared. The first
> run failed; the rerun against the changed file passed. No cause is inferred. Freeze and commit the
> reviewed files before relying on this handoff as an immutable baseline.

## 1. Verdict

Autotrade 7.0 is an **operational paper-trading MVP and research harness**. It is not a completed
Phase 1 system, not validated alpha, and not suitable for real-money trading.

The build currently provides:

- a hard-locked PAPER configuration;
- a NautilusTrader simulation account and active RiskEngine;
- public Gate.io REST ticker screening;
- an eight-symbol, cost-aware REST momentum tournament with one portfolio execution slot;
- paper positions, fees, slippage assumptions, stops, time exits, risk halts, persistence, and recovery;
- a localhost operations dashboard with fail-closed controls;
- an independent watchdog and Windows login auto-start flow;
- an optional, isolated TradingView research sidecar with zero execution influence;
- Python, lint, type, and Chromium critical-path tests.

The current bottleneck is **market-data and execution realism**, not dashboard polish. Five-second REST
quotes cannot validate a scalping strategy. WebSocket L2/trade capture, deterministic replay, and a
queue/latency/partial-fill-aware simulator must exist before strategy performance deserves serious
interpretation.

## 2. Handoff status

| Area | Status | Handoff conclusion |
| --- | --- | --- |
| Paper/live safety gate | Implemented and tested | Phase 1 rejects non-PAPER mode, live enablement, live confirmation, leverage above 1x, and public dashboard binding. |
| Nautilus runtime | Implemented | Simulation account, quote processing, and non-bypassed RiskEngine are verified. |
| Gate market data | Partial | Public REST ticker polling works; WebSocket, L2, trades, sequence recovery, and raw capture are absent. |
| Strategy lab | Partial | One REST momentum challenger exists; mean reversion and micro-maker challengers do not. |
| Paper execution | Partial | Spread, taker fees, adverse slippage, stops, time exit, PnL, and recovery exist; realistic fill mechanics do not. |
| Risk controls | MVP implemented | Single position, sizing, loss/drawdown, stale-data, manual pause, and recovery gates exist. Full specified limit set and fault injection are incomplete. |
| Persistence | Partial | Atomic paper checkpoint and append-only event audit exist; no replay dataset or operational database exists. |
| Dashboard | Operations MVP | Overview, markets, strategy state, open trade, 48-hour trade history, health, and controls exist. Full research/settings workflows are pending. |
| Windows operations | MVP implemented | Startup, login auto-start, health scan, limited restart, and safe auto-resume exist. A project stop script and disk-space check are missing. |
| TradingView sidecar | Implemented, optional | Read-only dashboard research; isolated from signals, risk, sizing, and execution. |
| Validation | Not complete | No historical replay, out-of-sample test, walk-forward test, or long-duration acceptance report. |
| Version control | **Blocker** | No commit exists; all project files are untracked. There is no immutable handoff revision. |

## 3. Objective and non-negotiable boundaries

The intended product is a local Windows application for researching Gate.io USDT perpetual-futures
scalping strategies around NautilusTrader. Phase 1 uses real public market data and simulated capital.

Hard constraints in the current build:

- PAPER ONLY; no exchange execution adapter or enabled real-order path;
- simulated starting balance of 300 USDT;
- maximum/default leverage of 1x;
- no martingale, DCA, forced trade quota, or forced daily return target;
- one execution authority: NautilusTrader;
- the dashboard is a read/control plane, not a trading engine;
- closing the browser does not stop the backend paper engine;
- stale or invalid state disables new entries;
- exchange credentials never enter the browser or current Phase 1 runtime;
- TradingView is advisory only and has zero execution weight.

## 4. Implemented architecture

```text
Gate public futures REST ticker endpoint
                  |
                  v
        validation and ranking
  volume / spread / score / freshness
                  |
                  v
     DashboardState + GatePoller thread
                  |
                  v
  PaperTrader-owned Nautilus BacktestEngine
  8 symbol observers -> candidate ranking -> 1 slot
                  |
          Nautilus RiskEngine
                  |
          simulated market orders
                  |
      positions / PnL / fees / events
          |                    |
          v                    v
 paper-state.json       paper-events.jsonl
          \                    /
           v                  v
          localhost HTTP API on 127.0.0.1:8767
                          |
                          v
                 native HTML/CSS/JS dashboard

TradingView Desktop -> loopback CDP -> pinned CLI -> TradingViewMonitor
                                                   -> dashboard card only
```

There are no microservices, broker, Redis instance, database server, React build, or cloud
dependencies. The backend uses Python standard-library HTTP and threading around NautilusTrader.

## 5. Build details

### 5.1 Runtime and configuration

`autotrade.__main__` exposes two commands:

- `smoke` (default): starts a Nautilus simulated account, processes one synthetic perpetual quote,
  proves the RiskEngine is active, writes a lifecycle event, and exits;
- `dashboard`: starts the long-running Gate REST paper engine and local dashboard.

Configuration is loaded from `config/paper.toml` and validated before runtime construction. Only the
three primary safety environment values and bounded TradingView flags can override relevant file
values. Unsafe values fail startup.

### 5.2 Gate market screening

The backend polls Gate's public USDT futures ticker endpoint every five seconds. Responses are limited
to 2 MB and must contain finite, positive bid/ask/last values, a non-crossed quote, non-negative quote
volume, change percentage, and funding rate.

Current selection behavior:

1. reject quote volume below 5,000,000 USDT;
2. reject spread above 12 basis points;
3. score remaining markets using capped 24-hour percentage change, logarithmic quote volume, and a
   spread penalty;
4. keep eight selected symbols;
5. retain the initialized monitoring universe when ranking updates, so the Nautilus engine is not
   rebuilt on every poll;
6. expose selected and rejected examples to the dashboard.

This is a transparent pilot heuristic, not a validated microstructure opportunity model.

### 5.3 REST momentum tournament

Each selected symbol has an independent `RestMomentumStrategy` inside one Nautilus account. A signal
must pass the configured movement, estimated cost, net-edge, confidence, persistence, and regime
checks. If several symbols qualify, the portfolio coordinator chooses the highest confidence, then
highest expected net edge, with a deterministic symbol tie-breaker.

Only one portfolio-wide position may exist. More than one open position is treated as an invariant
failure and halts the paper system.

Current strategy baseline:

| Setting | Value |
| --- | ---: |
| Selected/monitored symbols | 8 |
| Poll interval | 5 seconds |
| Short window | 12 observations |
| Persistence | 5 observations |
| Regime window | 36 observations, approximately 3 minutes |
| Entry movement threshold | 20 bps |
| Minimum modeled net edge | 12 bps |
| Minimum confidence | 0.60 |
| Paper notional | 30 USDT |
| Portfolio execution slots | 1 |
| Stop loss | 35 bps |
| Take profit | 55 bps |
| Maximum hold | 300 seconds |
| Cooldown | 60 seconds |
| Adverse slippage assumption | 2 bps per side |
| Taker fee model | 5 bps per side |
| Daily loss halt | 6 USDT |
| Maximum drawdown halt | 3% |

The strategy is an operational challenger only. It has not passed backtesting, holdout testing,
walk-forward testing, or sufficient forward-paper validation.

### 5.4 Paper execution and accounting

Implemented:

- Nautilus simulated market orders and positions;
- bid/ask spread crossing;
- 5 bps taker fee per side;
- configurable adverse slippage;
- 1x margin and position accounting;
- stop-loss, take-profit, time exit, cooldown, and manual flatten;
- split-fill aggregation into closed-trade history;
- signal, fill, position, rejection, and recovery events;
- rolling 48-hour closed-trade API view;
- atomic checkpoint replacement through a temporary file;
- restart recovery that blocks entries when a persisted position exists until manual flatten.

Not implemented:

- partial-fill probability driven by observed liquidity;
- queue-aware maker/post-only fills;
- measured acknowledgement, submission, or cancellation latency;
- stale resting-order behavior;
- funding debits and mark-price liquidation behavior;
- historical event replay and deterministic research runs;
- comparison between modeled fills and observed execution conditions.

Therefore, current paper PnL is research telemetry, not evidence of live expectancy.

### 5.5 Risk, failure, and security behavior

Implemented fail-closed controls include:

- startup rejection of any non-PAPER configuration;
- startup rejection of live flags or confirmation text;
- leverage capped at 1x;
- notional capped at 25% of starting capital;
- daily loss capped at 5% of starting capital by configuration validation;
- drawdown configuration capped at 5%;
- entry hurdle required to cover round-trip fee and slippage assumptions;
- stale Gate data pauses entries;
- strategy/execution exceptions halt entries;
- risk-limit breach halts entries;
- invalid persistent state blocks operation;
- recovery positions require a fresh quote and explicit flatten;
- manual pause remains sticky and is not watchdog-auto-resumable;
- dashboard bind is fixed to `127.0.0.1`;
- control requests validate local `Host` and `Origin` headers;
- no API secret is required, stored, logged, or sent to the UI in Phase 1.

The state API reports `ACTIVE`, `PAUSED`, or `HALTED`. Fresh public data can recover from transient
feed failure, but integrity and risk conditions remain blocked until the backend recovery gates allow
an explicit action.

### 5.6 Dashboard and local API

The dashboard is native HTML, CSS, and JavaScript served by the Python backend. It polls every five
seconds and maintains no browser-local portfolio state.

Implemented views include:

- PAPER mode and Gate venue identity;
- equity, daily PnL, drawdown, trades, exposure, fees, and modeled slippage;
- current open position with backend price;
- monitored market selection, rejection, signal state, and confidence;
- strategy state and portfolio execution-slot information;
- newest-first 48-hour closed-trade audit;
- engine, data, and TradingView health;
- clear alerts and backend-offline failure behavior;
- pause, resume, restart-feed, and flatten-paper controls.

Local endpoints:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/state` | Authoritative runtime snapshot |
| `POST` | `/api/control/pause` | Pause new paper entries |
| `POST` | `/api/control/resume` | Resume only if recovery gates permit |
| `POST` | `/api/control/restart-feed` | Request an immediate Gate refresh |
| `POST` | `/api/control/flatten` | Flatten simulated positions |
| `GET` | `/`, `/app.js`, `/styles.css` | Static dashboard assets |

There is deliberately no LIVE toggle.

### 5.7 Optional TradingView sidecar

TradingView MCP is pinned to commit `c05b8f5755ed8e64ea242de88ddbf46aa24d56a4` from
`tradesdontlie/tradingview-mcp`, package 1.0.0. The adapter:

- forces CDP to loopback port 9222;
- uses an argument-list subprocess with `shell=False`;
- applies a 2.5-second timeout, one retry, strict schema validation, 64 KiB output limit, TTL cache,
  stale detection, and a circuit breaker;
- reads bounded `status`, `quote`, and current `values` data;
- reports connected, degraded, stale, disconnected, unavailable, or disabled;
- never passes TradingView data into strategy, risk, sizing, orders, positions, or PnL;
- has configured execution weight `0.00`.

The local profile currently enables the observer. Failure is non-fatal to the Gate/Nautilus paper
system.

### 5.8 Windows operations

| Script | Purpose |
| --- | --- |
| `setup.bat` | Create `.venv` if needed and install pinned runtime/developer dependencies. |
| `check.bat` | Run Python tests, Ruff, and mypy; stop on first failure. |
| `start_bot.bat` | Run the one-shot Nautilus PAPER smoke. |
| `start_dashboard.bat` | Start the long-running local dashboard in the current terminal. |
| `check_ui.bat` | Run Chromium Playwright tests using the bundled Codex Node/Playwright runtime. |
| `setup_tradingview.bat` | Install or verify the exact pinned TradingView MCP checkout. |
| `start_tradingview_debug.bat` | Start/reuse TradingView Desktop CDP only if it is loopback-only. |
| `autostart_dashboard.bat` | Idempotently start the dashboard if unhealthy, start the watchdog, then open the UI. |
| `install_autostart.bat` | Add a current-user Windows Startup launcher. |
| `health_check.bat --once` | Run one immediate health/recovery scan. |
| `health_check.bat` | Run a singleton scan loop every 900 seconds. |

The watchdog verifies the API state, exactly one listener on the dashboard port, listener ownership,
PAPER mode, Nautilus readiness, live/fresh Gate data, strategy state, and TradingView state. It can
restart an unavailable dashboard once, safely resume only when the backend grants
`auto_resume_allowed`, and recover the TradingView sidecar without touching the engine.

## 6. Persistence and logs

| Artifact | Behavior |
| --- | --- |
| `logs/paper-state.json` | Atomic paper checkpoint: balance, trade count, fees, and optional position/recovery state. |
| `logs/paper-events.jsonl` | Append-only strategy, signal, fill, position, and recovery audit. |
| `logs/engine_*.jsonl` | Nautilus JSON engine logs with configured size/count rotation. |
| `logs/engine-lifecycle.jsonl` | One-shot smoke completion audit. |
| `logs/dashboard.log` | Rotating local dashboard/backend log. |
| `logs/health-check.log` | Watchdog decisions and recovery audit. |

`paper-state.json` is replaced atomically. `paper-events.jsonl` is append-only but currently has no
rotation or archival policy, so long-run disk growth remains an operational gap.

## 7. Verification evidence

### 7.1 Current automated checks

Executed from the workspace on 2026-08-30 around 15:38–15:39 WIB:

```bat
check.bat
check_ui.bat
```

| Check | Result | Notes |
| --- | --- | --- |
| Python unit/integration suite | **39 passed** | Configuration, dashboard, market ranking, stale recovery, origin checks, paper strategy/execution/recovery/history, Nautilus lifecycle, and TradingView isolation. |
| Ruff 0.16.4 | **Passed** | No issues in the current checked source set. |
| mypy 2.3.1 | **Passed** | No issues in 12 source files. |
| Playwright Chromium | **3 passed** in 9.2 s | Truthful paper UI, backend-loss failure state, and TradingView isolation/responsive states. |

The Python run emits `Pandas4Warning` messages from Nautilus execution paths because
`Timestamp.utcnow` is deprecated. They do not fail current tests, but they are dependency-compatibility
warnings to monitor before a pandas/Nautilus upgrade.

### 7.2 Live local snapshot

Observed from `http://127.0.0.1:8767/api/state` at approximately 15:38 WIB:

| Field | Observed value |
| --- | ---: |
| Mode / venue | PAPER / GATE |
| Trading state | HALTED; explicit resume allowed by the backend at that snapshot |
| Engine | SIMULATION_READY; RiskEngine enabled |
| Gate data | LIVE; public REST; 5.6 s age; 937 ms request latency |
| Starting balance | 300.00000000 USDT |
| Equity | 294.71162599 USDT |
| Daily/closed PnL | -5.28837401 USDT |
| Drawdown | 1.76279134% |
| Closed trades | 35 |
| Open positions | 0 |
| Accumulated fees | 1.04556002 USDT |
| Modeled slippage | 0.41822401 USDT |
| TradingView | CONNECTED; correct ETH perpetual/five-minute chart; zero execution influence |

This is a volatile runtime snapshot, not a performance conclusion. Thirty-five trades from one short
paper session are statistically insufficient, and the current simulator omits several material
execution costs.

## 8. Repository map

| Path | Responsibility |
| --- | --- |
| `autotrade/config.py` | TOML/env loading and Phase 1 safety validation. |
| `autotrade/runtime.py` | One-shot Nautilus safety smoke. |
| `autotrade/paper.py` | Momentum observers, coordinator, simulated execution, PnL, risk, persistence, recovery, and history. |
| `autotrade/dashboard.py` | Gate REST polling/ranking, state authority, local API, static serving, logging, and runtime orchestration. |
| `autotrade/tradingview.py` | Strict read-only TradingView subprocess adapter and isolated monitor. |
| `dashboard/` | Native operations UI assets. |
| `config/paper.toml` | Current paper baseline. |
| `tests/` | Python unit/integration tests and Playwright browser audit. |
| `docs/` | Specifications, roadmap, operations, security, and research boundaries. |
| `vendor/tradingview-mcp.pin` | Reviewed TradingView upstream revision and runtime pin. |
| `health_check.ps1` | Independent health/recovery loop. |
| root `.bat` files | Setup, test, startup, auto-start, and sidecar operations. |

## 9. Known gaps and risks

### P0 — Must resolve before treating the handoff as stable

1. **Create an immutable Git baseline.** Review secrets and generated files, stage the intended
   project files, then make the initial commits. The current repository has no recoverable revision.
2. **Stop concurrent file changes during acceptance.** Re-run `check.bat` and `check_ui.bat` on the
   exact files that will be committed.
3. **Keep PAPER ONLY.** Do not add Gate private credentials or an execution adapter during this phase.

### P1 — Critical path to meaningful strategy research

1. Implement Gate WebSocket L2 and trade ingestion with exchange/local timestamps, sequence-gap
   recovery, crossed-book checks, reconnect, and raw append/replay capture.
2. Build deterministic replay and a simulator with measured latency, partial fills, funding, and
   conservative queue-aware maker logic.
3. Run historical, holdout, sensitivity, and walk-forward validation before modifying the strategy
   in response to short paper losses.
4. Add long-duration stability and fault-injection evidence for process death, feed gaps, corrupt
   state, persistence failure, and disk pressure.

### P2 — Important after the data/simulator foundation

1. Add independent order-flow, mean-reversion, and micro-maker challengers with per-strategy metrics.
2. Complete research/settings dashboard pages only when authoritative backend datasets exist.
3. Add rotation/retention for `paper-events.jsonl` and a watchdog free-disk threshold.
4. Add a project-scoped stop script and document graceful shutdown/restart behavior.
5. Reconcile documentation drift:
   - roadmap says 10 tests, TradingView notes say 28, while the current Python suite has 39;
   - risk documentation says three-tick persistence, while current configuration uses five;
   - TradingView documentation says the default remains false, while the current paper profile and
     feature table enable it;
   - `OPERATIONS_WINDOWS.md` lists `stop_bot.bat`, but that file does not exist.
6. Track the Nautilus/pandas deprecation warnings during dependency upgrades.

### P3 — Ignore for now

- live Gate execution;
- leverage above 1x;
- VPS/cloud migration;
- multi-exchange support;
- Redis, queues, microservices, Kubernetes, React migration, or a mobile application;
- LLM/AI decision-making in the trading hot path;
- cosmetic dashboard expansion without new authoritative research data.

## 10. Critical assumptions

**Critical assumption:** five-second REST best-bid/ask observations are useful enough to operate a
temporary forward-paper challenger.

**If wrong:** apparent entries, exits, costs, and PnL do not resemble executable scalping conditions,
so strategy conclusions are misleading.

**How to verify cheaply:** capture synchronized Gate L2/trades and compare REST-observed decisions
against replayed order-book conditions before adding strategy complexity.

**Critical assumption:** the current 5 bps taker fee and 2 bps-per-side adverse slippage model is
conservative enough for screening.

**If wrong:** the 20 bps entry hurdle can admit trades with negative real net expectancy.

**How to verify cheaply:** measure fee tier, spread, update-to-decision delay, and forward trade/book
movement from the captured dataset, then rerun the same signals without changing strategy rules.

## 11. Clean handoff runbook

From a fresh PowerShell or Command Prompt in the project root:

```bat
setup.bat
setup_tradingview.bat
check.bat
check_ui.bat
start_bot.bat
start_dashboard.bat
```

Then open:

```text
http://127.0.0.1:8767/
```

For one watchdog check:

```bat
health_check.bat --once
```

After manual startup and browser checks are accepted, enable current-user login startup:

```bat
install_autostart.bat
```

Expected safe startup state:

- mode is `PAPER`;
- engine is `SIMULATION_READY`;
- RiskEngine is enabled;
- Gate data becomes `LIVE` and fresh;
- new entries remain halted until a permitted paper resume;
- no LIVE control or exchange credential appears;
- TradingView failure, if any, is displayed but does not affect Gate/Nautilus controls.

## 12. Acceptance criteria for the next handoff

The next owner should not call Phase 1 complete until all of the following are true:

- an immutable, reviewed Git revision identifies the build;
- all Python, Ruff, mypy, and Playwright checks pass on that revision;
- real Gate WebSocket L2/trade data is captured with integrity and timestamp evidence;
- captured data replays deterministically;
- paper fill assumptions include measured latency, funding, partial-fill, and queue uncertainty;
- risk and recovery fault-injection tests pass;
- long-duration paper stability is documented;
- strategy reports include net costs, sample size, expectancy, profit factor, drawdown, MFE/MAE,
  symbol/regime breakdown, holdout results, and walk-forward evidence;
- no real-order path or secret exposure has been introduced.

## 13. Exact next action

**Freeze the working tree, review it, and create the first small Git commits. Then rerun both check
scripts against the committed revision.** Only after that baseline exists should work continue on the
Gate WebSocket/L2/trade recorder, which is the current technical bottleneck.
