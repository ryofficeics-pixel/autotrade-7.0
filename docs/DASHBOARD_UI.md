# Dashboard and UI Specification

## Market Scope Control

The overview includes a prominent backend-authoritative `MARKET SCOPE` control with `WIDE CRYPTO`
and `XAU ONLY` choices plus the three switch policies. A click first shows `SWITCHING`; the active
indicator changes only after the backend response. `FLATTEN_AND_SWITCH` requires explicit browser
confirmation and is never the default.

The readout shows active scope, execution symbol, allowed decisions, requested scope, switch state,
and blocking reason. In XAU scope, the broad scanner is hidden without being removed and a dedicated
panel shows XAU price, `LONG/SHORT/WAIT`, confidence, regime, volatility, confirmation health,
position, entry, PnL, stop, target, leverage, and risk. Analytics provides ALL, CRYPTO, and XAU views.

Scope controls use native buttons and a native select, preserve visible keyboard focus, expose
`aria-pressed`, and use 44 px targets on small screens. Failure and pending states remain visible and
never imply activation before backend confirmation.

## TradingView Secondary Card

Show the optional TradingView observer inside System Health, visually subordinate to Gate/Nautilus.
Render `CONNECTED`, `DEGRADED`, `STALE`, `DISCONNECTED`, `UNAVAILABLE`, or `DISABLED` truthfully and
show chart symbol/timeframe, diagnostic fields, latency and freshness. The card must always state or
imply research-only/no execution influence. A TradingView failure must not turn bot health red or
disable paper controls.

## Product Direction

The UI should feel like a professional trading operations console, not an exchange clone.

Reference useful operational concepts from Hummingbot dashboards/Condor, but do not copy the legacy Streamlit UI wholesale. Hummingbot's older dashboard is no longer actively maintained.

NautilusTrader is primarily an engine and visualization/research toolkit, not a complete operational dashboard, so this app should provide the missing face around it.

References:
- https://hummingbot.org/dashboard/
- https://nautilustrader.io/docs/latest/concepts/

## Visual Language

Design read: this is a dense operations console whose first job is to expose unsafe state and
evidence limits, not a marketing surface. ENERGY 2, RHYTHM 2, MOTION 1. The restrained cyan square,
hard rectangular controls, compact type, and status text are the identity motif. Subtle dark
gradients separate control/evidence hierarchy without implying motion or depth; cyan is reserved for
PAPER-safe/valid state, amber for review, and red for failure. The dark theme matches the existing
long-running monitoring workflow; no alternate theme is specified for Phase 1.

- Dark-first professional interface
- Dense but readable
- High information hierarchy
- Minimal decoration
- Monospace only for numbers/logs where useful
- Strong state indicators
- Responsive desktop-first
- No fake animation
- No unnecessary charting

## Primary Layout

```text
+-------------------------------------------------------------+
| SCALPER | PAPER | Gate.io | ● ENGINE | ● DATA | 18 ms      |
+----------+--------------------------------------------------+
| NAV      | EQUITY  $300.00      TODAY PNL      DRAWDOWN     |
|          | TRADES  24            WIN RATE       EXPOSURE     |
| Overview +--------------------------------------------------+
| Markets  | Active Strategies                               |
| Strategy | OrderFlow A   RUNNING   PF ...   Exp ...         |
| Trades   | MeanRev B     SHADOW    PF ...   Exp ...         |
| Research | MicroMaker C  SHADOW    PF ...   Exp ...         |
| Health   +--------------------------------------------------+
| Settings | Open Positions / Recent Fills                    |
|          +--------------------------------------------------+
|          | System Health / Alerts                           |
+----------+--------------------------------------------------+
```

## Pages

### 1. Overview

Show only decision-relevant operations:

- mode: PAPER;
- capital/equity;
- daily net PnL;
- drawdown;
- trades today;
- open positions;
- the current backend market price for an open paper trade;
- active symbols;
- active/challenger strategies;
- fees/slippage;
- data latency;
- engine health;
- last market event;
- pause/resume.

### 2. Markets

For each candidate symbol:

- volatility score;
- spread;
- liquidity score;
- book depth;
- trade rate;
- opportunity score;
- per-symbol signal confidence and state;
- selected/not selected.

Do not show hundreds of symbols by default.

### 3. Strategies

Cards/table for each strategy:

- status;
- sample size;
- net expectancy;
- PF;
- drawdown;
- average holding time;
- recent performance;
- regime compatibility;
- champion/challenger status.

### 4. Trades

Filterable audit trail:

- timestamp;
- symbol;
- strategy;
- side;
- intended edge;
- fill;
- fees;
- slippage;
- PnL;
- exit reason.

The current Phase 1 view shows the last 48 hours of authoritative timestamped closed-paper trades,
newest first. It includes open and close fill prices, net realized PnL in USDT and percent, combined
entry/exit fees, side and exit reason. Split fills are aggregated into full quantities,
quantity-weighted entry/exit prices and complete fees before the displayed PnL percentage is computed.
Legacy event rows without timestamps are omitted rather than
assigned an invented time. The open-position ticker sits directly below the equity ribbon and refreshes
from the backend Gate price every five seconds.

### 5. Research

- backtest runs;
- comparison table;
- MFE/MAE;
- forward-return horizon analysis;
- equity curve;
- cost decomposition.

The implemented Research section is a bounded read-only projection of
`reports/research-latest.json`. It shows exact dataset/experiment identity, the Strategy Lab comparison,
candidate funnel and binding gates, avoided-loss versus missed-profit diagnostics, decomposed
execution edge, and lifecycle state. Unsupported comparisons say `NOT_RECONSTRUCTABLE`; missing or
corrupt reports show `UNAVAILABLE` or `ERROR`. Signal strength is never labeled as calibrated
confidence. There is no browser promotion, activation, parameter-tuning, or LIVE control.

### 6. Health

- market feed;
- book validity;
- engine;
- database;
- watchdog;
- API;
- clock/timing;
- last reconnect;
- current error state.

The overview must show a prominent accounting banner independently from trading state. Health
diagnostics expose accounting, risk, market data, strategy and execution-model states plus run/session,
Git/config identity, event/checkpoint sequences and disk headroom. `Resume` is disabled whenever
accounting is not `VALID`. Per-symbol quarantine is shown in the market table and must not imply a
global account failure.

### 7. Settings

Only safe Phase 1 settings.

No LIVE toggle.

## Controls

Primary emergency control:
- `PAUSE NEW ENTRIES`

Separate:
- `FLATTEN PAPER POSITIONS`
- `RESTART DATA FEED`

Never combine them into one ambiguous button.

`ANALYZE & AUTO-FIX` is available even while halted. It shows the detected cause, checks
performed, repairs completed, and any remaining action. It retries the public feed, checks
durable accounting, and safely resumes startup pauses. It preserves manual pauses and critical
halts. Recovered open positions require the separate `FLATTEN PAPER POSITIONS` action followed
by `RESUME PAPER`; risk limits, uncertain execution, and corrupt evidence are never bypassed.
The result remains visible on refresh, controls stay disabled during a request, and failures
are shown explicitly. An unreachable backend requires the existing local launcher/watchdog.

The operating-state header shows halt timing without implying automatic recovery. Current-day
`DAILY_LOSS` and `MAX_DRAWDOWN` halts count down to UTC rollover, labeled as manual-review eligibility.
`DAILY_LOSS_REVIEW`, `MAX_DRAWDOWN_REVIEW`, and other critical halts show that manual review is
required and no automatic resume occurs.

`START NEW PAPER RUN` is the in-app replacement for the `new-paper-run --confirm-new-run`
command. It is available only when a reconciled PAPER account is flat, market data is fresh,
storage is safe, and the current halt is `MAX_DRAWDOWN`. It requires explicit confirmation,
archives the completed run, creates a new run identity and ledger, then starts that new PAPER
experiment. It never clears or relabels the halted run and is not a LIVE control.

## Charts

Keep charts purposeful:

- equity curve;
- rolling drawdown;
- net expectancy by strategy;
- cost breakdown;
- latency history.

Order-book visualization is optional and should not block Phase 1.

## UI State Accuracy

Every dashboard metric must come from authoritative backend state.

Never calculate portfolio truth separately in the browser.

## AMA Control Panel

The AMA control panel remains visible in both market scopes. It shows current XAU price, KAMA 10/20/50,
regime, raw and filtered signals, hysteresis, quality-gate decision, reference state, evidence integrity,
and observation count. A comparison table shows raw, filtered, fair-value reference, combined, and V3
rows. Unsupported families must say `NOT IMPLEMENTED`, `REFERENCE ONLY`, or `SEPARATE CAPTURE TIMELINE`;
the browser must not synthesize performance.

The panel must state that execution influence is none, show insufficient samples explicitly, and never
offer promotion or execution controls. Loading, backend-loss, empty, and mobile states use the same
fail-closed behavior as the rest of the dashboard.

The wide-crypto extension preserves the same ENERGY 2, RHYTHM 2, and MOTION 1 operations-console
direction. Strategy Tournament adds comparable wide-crypto rows without hiding retired or failed
families. Universe, Top Candidates, and Overfitting Audit are evidence tables, not decorative cards.
All wide tables stay inside horizontal wrappers on mobile, and unavailable statistical measures show
their real status instead of invented values.
