# Market Scope implementation note

Date: 2026-09-18

## Audit summary

The current runtime has one executable entry path. `GatePoller` obtains public Gate futures data,
`rank_tickers` screens the wide universe, and `DashboardState.apply_snapshot` sends that snapshot to
`PaperTrader.process`. `PaperTrader` owns the REST Momentum strategies, final candidate selection,
Nautilus RiskEngine, simulated orders, positions, ledger events, atomic checkpoints, daily risk,
and restart recovery. The browser is a read and control plane only.

Related components:

- scanner and universe ranking: `autotrade/dashboard.py`
- crypto signal and final entry submission: `autotrade/paper.py`
- WebSocket capture and V3 shadow research: `autotrade/capture.py`, `autotrade/entry_v3.py`
- global risk, execution, positions, persistence, and recovery: `autotrade/paper.py`,
  `autotrade/integrity.py`
- analytics and experiment primitives: `autotrade/analytics.py`, `autotrade/experiments.py`
- persistent configuration: `autotrade/config.py`, `config/paper.toml`
- dashboard and local HTTP API: `autotrade/dashboard.py`, `dashboard/`
- watchdog and health: `health_check.ps1`, `autotrade/dashboard.py`

No production self-learning or automatic strategy-weight mutation exists. V3 is a separate shadow
research path and must remain non-executing. Performance analytics are ledger-derived.

## Placement

Add one shared `MarketScopeController` between profile-specific signal routing and the existing
`PaperTrader.process` entry gate:

```text
Gate public data
  -> WIDE_CRYPTO scanner and REST Momentum profile
  -> XAU_ONLY XAU signal and confirmation profile
  -> MarketScopeController
  -> one final PaperTrader entry guard
  -> existing risk, position, Nautilus paper execution, ledger, and recovery
```

The controller owns the active scope, requested scope, switch policy, explicit transition state,
and atomic restart state. It never owns orders or positions. Existing positions remain owned by the
common position manager after a scope change.

## Safety boundaries

- Old configuration defaults to `WIDE_CRYPTO`.
- Phase 1 remains PAPER only. This revision will not add a live order adapter or weaken the existing
  startup rejection of LIVE configuration.
- `SWITCH_WHEN_FLAT` is the default. `FLATTEN_AND_SWITCH` requires an explicit confirmation value.
- A pending transition blocks new entries until its state permits them.
- XAU execution never falls back to XAUT, PAXG, XAU5L, XAU5S, or another symbol.
- XAUT and PAXG are optional normalized-return confirmations only.
- Global accounting, persistence, recovery, freshness, and drawdown gates remain absolute.
- The existing partial-runner test currently fails on checkpoint and ledger entry-fee reconciliation.
  That pre-existing failure must be corrected before the mode-switch work can be called complete.

## Gate contract verification

The repository uses Gate futures contract names such as `BASE_USDT`. A read-only Gate API check on
2026-09-18 found `XAU_USDT`, `XAUT_USDT`, and `PAXG_USDT` enabled and not delisting. For `XAU_USDT`,
Gate reported price step `0.01`, minimum size `1` contract, contract multiplier `0.0001`, leverage
range `1` to `100`, and a four-hour funding interval. The bot will still discover and validate these
values at runtime. XAU mode must block entries if current metadata is missing or unsafe.

## UI direction

The existing dashboard specification is the design source: a dark, dense trading operations console
with minimal decoration and motion. The Market Scope control will reuse that visual language.

Design read: operations console for one operator, ENERGY 1 / RHYTHM 1 / MOTION 1. The active scope
and transition state are the focal decision. No new theme, decorative icon set, or animation is needed.
