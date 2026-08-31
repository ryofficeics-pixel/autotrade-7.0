# Dashboard and UI Specification

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
