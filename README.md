# Scalper Bot — NautilusTrader Core

## Purpose

A local-first crypto perpetual futures scalping application built around NautilusTrader, with Hummingbot used as a strategy/reference source rather than a second execution engine.

Phase 1 runs on a Windows laptop, uses real Gate.io market data, trades with simulated capital only, starts at 1x leverage, and hard-locks all real orders.

## Phase 1 Baseline

- Engine: NautilusTrader
- Exchange: Gate.io USDT perpetual futures
- Capital: USD 300 paper
- Leverage: 1x
- Environment: Windows laptop
- Live orders: HARD DISABLED
- Expected trade frequency: about 20–30 completed trades/day, never quota-driven
- Pair selection: dynamically rank volatile pairs after liquidity/spread filters
- Holding period: short and data-optimized, not arbitrarily fixed
- Strategy candidates:
  - Order-flow / momentum
  - Mean reversion
  - Micro market making / spread capture
- Hummingbot: reference implementation / source of reusable strategy ideas
- Dashboard: custom web UI
- Browser audit: Playwright
- Coding discipline: Ponytail principles for minimal safe code

## Core Principle

The application must optimize net expectancy after fees, spread, slippage, latency, funding and fill quality.

More trades are not the objective. More positive-expectancy trades are.

## Documentation

See `docs/`.

Recommended reading order:

1. `PROJECT_SCOPE.md`
2. `ARCHITECTURE.md`
3. `TRADING_SPEC.md`
4. `DATA_SPEC.md`
5. `STRATEGY_LAB.md`
6. `RISK_MANAGEMENT.md`
7. `BACKTEST_VALIDATION.md`
8. `DASHBOARD_UI.md`
9. `TESTING_PLAYWRIGHT.md`
10. `OPERATIONS_WINDOWS.md`
11. `SECURITY.md`
12. `CODEX_WORKFLOW.md`
13. `AUTOTRADE_7_IMPROVEMENT_AUDIT.md`
14. `RESEARCH_ENGINE.md`

## Safety Gate

Production/live order routing must not exist as an enabled path in Phase 1.

Required defaults:

```env
TRADING_MODE=PAPER
LIVE_TRADING_ENABLED=false
LIVE_CONFIRMATION=
```

A future live mode must require a separate explicit implementation phase, dedicated API credentials and multiple independent checks.

## Phase 1 Runtime

`start_bot.bat` runs the one-shot NautilusTrader safety smoke. `start_dashboard.bat` runs the
long-lived local paper system: Gate public REST screening, an eight-pair cost-aware REST-momentum
tournament with one execution slot, Nautilus simulated orders/positions, risk controls, and the operations dashboard. No exchange
execution adapter or live-order path exists.

On Windows:

```bat
setup.bat
setup_tradingview.bat
check.bat
start_bot.bat
start_dashboard.bat
stop_bot.bat
install_autostart.bat
capture_market_data.bat 3600
```

Configuration lives in `config/paper.toml`. The three safety environment variables shown above may
override the file only if they remain paper-safe; any live value fails startup.

The dashboard runs at `http://127.0.0.1:8767`. It uses only Gate's public futures ticker endpoint,
keeps paper controls fail-closed until a fresh snapshot is explicitly resumed, and never receives
exchange credentials. The initial challenger monitors every screened pair, uses 30 USDT notional at
1x, permits one portfolio-wide position for the highest-confidence eligible signal, and retains its
cost hurdle, stops, time exits, cooldown, and hard session loss/drawdown limits. Zero trades remains valid when
the modeled edge does not exceed costs. `health_check.bat` runs a singleton local health scan every
15 minutes and safely resumes PAPER after a clean restart only when the backend grants
`auto_resume_allowed`; manual and safety halts remain sticky. Use `health_check.bat --once` for an
immediate terminal check. Run `check_ui.bat` for the Chromium critical-path audit in Codex's bundled
Playwright runtime.

## Market Scope

The dashboard and backend expose one persistent entry-routing scope:

- `WIDE_CRYPTO` preserves the existing dynamic crypto screen and tournament.
- `XAU_ONLY` permits new entries only in Gate `XAU_USDT` perpetual. It uses its own configurable
  signal and risk profile, with `XAUT_USDT` and `PAXG_USDT` as optional confirmation feeds.

Both scopes use the same `PaperTrader`, Nautilus account, final entry guard, risk controls, position
management, append-only ledger, and restart reconciliation. XAU decisions are `LONG`, `SHORT`, or
`WAIT`; restricting the universe never forces a position. Scope state is atomically persisted to
`logs/market-scope.json`, while changes are appended to `logs/market-scope-events.jsonl`.

The switch policies are `SWITCH_WHEN_FLAT` (default), `SWITCH_NOW_KEEP_EXISTING`, and the explicitly
confirmed `FLATTEN_AND_SWITCH`. A pending drain or flatten blocks every new entry while the existing
position remains under the common manager. The API is `GET/POST /api/market-scope`.

Relevant settings are centralized in `[market]`, `[mode_switch]`, `[xau]`,
`[xau.confirmations]`, and `[xau.risk]` in `config/paper.toml`. Missing market-scope settings migrate
to `WIDE_CRYPTO`. Phase 1 still rejects every LIVE configuration; XAU support does not create a live
order path.

`install_autostart.bat` installs a current-user Windows Startup launcher. At login it starts the
dashboard only when the health endpoint is unavailable, waits up to 30 seconds for health, starts the
local 15-minute watchdog, and then opens `http://127.0.0.1:8767/` in the default browser. Run
`autostart_dashboard.bat` directly to use the same behavior immediately.

Paper recovery never resets a checkpoint to the configured balance. Schema-v3 runs reconcile their
checkpoint, current-run event ledger and Nautilus opening balance before Resume is allowed. Existing
legacy evidence remains visible but blocked as `STATE_INVALID`. After stopping the dashboard, an
operator may explicitly archive the old evidence and prepare a separate experiment with:

```bat
.venv\Scripts\python.exe -m autotrade new-paper-run --config config\paper.toml --confirm-new-run
```

`capture_market_data.bat` runs an isolated public-data research capture for the requested number of
seconds (one hour by default) under `data/gate-captures`. It does not feed the paper trader. Verify a
completed dataset and reproduce its final local-book digest with:

```bat
.venv\Scripts\python.exe -m autotrade replay-verify --dataset data\gate-captures\DATASET_ID
```

## Independent XAU KAMA Control

The PAPER service now runs `XAU_KAMA20_CONTROL_V2` continuously from the retained XAU/XAUT/PAXG REST
poll, regardless of entry scope. It compares raw and filtered KAMA20 shadows on one observation
timeline, records deterministic rejection reasons and later counterfactual outcomes, and publishes
read-only strategy/reference/realism reports under `reports/`.

This control is research-only. Configuration rejects `xau.ama_control.execution_enabled = true`, the
engine has no order-submission reference, and the dashboard has no AMA execution control. V2 enforces
the documented price-plus-KAMA20-slope raw signal and keeps its evidence separate from the superseded
pre-validation V1 stream. Its quote-step
ATR is not candle ATR, and its modeled profiles do not prove achievable fills.

## Offline Research and Promotion Evidence

The research CLI freezes the current verified AMA evidence prefix, replays the raw KAMA, filtered
KAMA, fair-value, and combined challengers through one causal interface, and emits candidate-level
ledgers, counterfactuals, explicit cost decomposition, chronological validation, and a sealed final
holdout. It also diagnoses the captured Entry V3 funnel without pretending its separate L2 timeline
can be reconstructed from REST observations.

```bat
.venv\Scripts\python.exe -m autotrade.research_cli --project-root . full
```

The latest read-only summary appears in the dashboard Research section. Research cannot arm a
strategy or clear `STRATEGY_EVIDENCE_FAILED`; all current execution profiles remain modeled and
manual PAPER activation is a separate lifecycle operation. See `docs/RESEARCH_ENGINE.md`.

## Optional TradingView Research Sidecar

The pinned TradingView MCP checkout is an optional local health/research sidecar. The paper profile
reads a bounded quote and current indicator values for a dashboard-only second opinion with zero
execution weight. Gate remains authoritative, and TradingView cannot influence signals, risk, sizing,
paper orders or future exchange execution.

Run `setup_tradingview.bat` to verify/install the pinned checkout. After separately installing
TradingView Desktop, `start_tradingview_debug.bat` launches it without killing an existing instance
and verifies that CDP port 9222 is loopback-only. Read `docs/TRADINGVIEW_INTEGRATION.md`; upstream usage
restrictions prohibit using extracted data for automated trading decisions.

## Reference Projects

- NautilusTrader: https://github.com/nautechsystems/nautilus_trader
- Nautilus docs: https://nautilustrader.io/docs/
- Hummingbot: https://github.com/hummingbot/hummingbot
- Hummingbot strategies: https://hummingbot.org/strategies/
- Hummingbot dashboard reference: https://hummingbot.org/dashboard/
- Ponytail: https://github.com/DietrichGebert/ponytail
- Playwright: https://playwright.dev/
- TradingView MCP: https://github.com/tradesdontlie/tradingview-mcp

## Wide-Crypto Offline Research

This workflow uses only public Gate data and cannot place orders:

```powershell
.venv\Scripts\python.exe -m autotrade.wide_crypto_cli freeze --project-root .
.venv\Scripts\python.exe -m autotrade.wide_crypto_cli verify --project-root . --dataset research\datasets\wide-crypto-v1-...
.venv\Scripts\python.exe -m autotrade.wide_crypto_cli full --project-root .
```
