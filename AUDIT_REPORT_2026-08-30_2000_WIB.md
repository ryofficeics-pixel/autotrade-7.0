# Autotrade 7.0 — Full Trading Audit

## Audit control

| Field | Value |
| --- | --- |
| Requested report | 2026-08-30 20:00 WIB |
| Actual audit cutoff | 2026-08-31 08:56:06 WIB / 2026-08-31T01:56:06Z |
| Workspace | `G:\codex\autotrade 7.0` |
| Runtime at cutoff | PAPER / GATE / NautilusTrader 1.231.0 |
| Source event file | `logs/paper-events.jsonl`, 346 lines, 57,132 bytes |
| Earliest retained event | Line 1, legacy signal without timestamp |
| Earliest timestamped event | Line 42, 2026-08-28T02:26:32.854323Z |
| Last retained event | Line 346, 2026-08-30T12:04:07.835395Z |
| Log parse result | 346/346 JSON objects parsed; 0 malformed lines |
| Git state | `main`, unborn HEAD; all project files remain untracked |

## Executive verdict

**Go/no-go: pause the paper strategy for an accounting/recovery fix. Do not promote it, tune it for
profit, or add live execution.**

The retained record contains 51 closed trades, including one recovery flatten, with a reconstructed
realized PnL of **-6.56405410 USDT** for the 45 trades whose close records are timestamped. Six earlier
trades are present but lack timestamps, symbol, side, quantity, prices, and close fees. The full
system checkpoint reports 51 trades, 292.99226546 USDT, and a DAILY_LOSS halt, while the live API at the
cutoff reports 300.00 USDT equity and zero trades in the current window. That mismatch is a state
integrity defect until reproduced and explained.

The strategy result is decisively poor in the retained sample: 8 wins, 37 losses, 17.78% win rate,
0.119 profit factor, and -0.14586787 USDT expectancy per recorded closed trade. The result is not a
valid estimate of live profitability because the system uses five-second REST quotes and lacks L2,
trade-flow, measured latency, partial-fill, funding, and queue-aware execution modeling.

## 1. Evidence set and coverage limits

Files inspected:

- `logs/paper-events.jsonl`
- `logs/paper-state.json`
- `logs/dashboard.log`
- `logs/health-check.log`
- `logs/engine-lifecycle.jsonl`
- current local `GET http://127.0.0.1:8767/api/state`
- `autotrade/config.py`, `autotrade/paper.py`, `autotrade/dashboard.py`, `autotrade/runtime.py`,
  `autotrade/tradingview.py`
- `config/paper.toml`, tests, `PROJECT_HANDOFF.md`, and `docs/`

The event file is internally valid JSON, but it is not a complete audit trail from system inception:

1. Lines 1–36 contain six complete-looking trade cycles whose close records are legacy rows with no
   `timestamp_utc`, `symbol`, `side`, `quantity`, `open_price`, `close_price`, or `fee_usdt`.
2. Their preceding fill rows contain prices and fees, but identity is also absent. The records can be
   counted and their PnL values included, but cannot be assigned reliably to a symbol or exact time.
3. The file begins at line 1, but there is no external creation marker proving it is the first event
   ever generated. Earlier files may have been rotated, deleted, or overwritten before this audit.
4. Engine logs contain many restarts and smoke runs but do not replace the paper event ledger.

Consequently, this report distinguishes **all retained events** from **fully attributable trades**.

## 2. Event inventory

| Event | Count | Coverage note |
| --- | ---: | --- |
| `signal` | 51 | Six legacy rows lack timestamp/symbol metadata. |
| `fill` | 143 | Includes split exits; six legacy cycles lack symbol metadata. |
| `position_opened` | 51 | One per retained closed/recovery cycle. |
| `exit_signal` | 50 | Recovery flatten has no normal strategy exit signal. |
| `position_closed` | 50 | 44 timestamped normal closes plus 6 legacy closes. |
| `recovery_flatten` | 1 | Timestamped MSTRX recovery close. |
| **Total** | **346** | 0 malformed JSON lines. |

Other event facts:

- 51 closed records exist: 50 `position_closed` plus 1 `recovery_flatten`.
- 45 close records are timestamped and attributable; 6 are legacy.
- The first timestamped close is ETH_USDT at line 42, 2026-08-28T02:26:32.854323Z.
- The last close is HNT_USDT at line 346, 2026-08-30T12:04:07.835395Z.
- There are no unmatched timestamped opens after sequential symbol matching, but this does not prove
  historical completeness because legacy identity is absent.

## 3. Complete retained closed-trade ledger

Rows are in event-file order. `LEGACY` means the close record did not contain the required identity,
time, price, quantity, or fee fields. PnL is the value recorded by the engine, not a recomputation.

| Line | Closed UTC | Symbol | Side | Quantity | Open | Close | Net PnL USDT | Fee USDT | Exit reason |
| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| 6 | LEGACY | unknown | unknown | — | — | — | -0.09168755 | — | unknown |
| 12 | LEGACY | unknown | unknown | — | — | — | -0.05144722 | — | unknown |
| 18 | LEGACY | unknown | unknown | — | — | — | -0.04399903 | — | unknown |
| 24 | LEGACY | unknown | unknown | — | — | — | -0.14155264 | — | unknown |
| 30 | LEGACY | unknown | unknown | — | — | — | -0.02410502 | — | unknown |
| 36 | LEGACY | unknown | unknown | — | — | — | -0.09088898 | — | unknown |
| 42 | 2026-08-28 02:26:32.854323Z | ETH_USDT | SHORT | 0.011956 | 2489.60 | 2497.36 | -0.12259061 | 0.02981205 | TIME_EXIT |
| 48 | 2026-08-28 02:50:40.372964Z | ETH_USDT | SHORT | 0.011956 | 2487.23 | 2488.66 | -0.04684295 | 0.02974587 | TIME_EXIT |
| 54 | 2026-08-28 03:17:31.382095Z | ETH_USDT | LONG | 0.012049 | 2491.27 | 2490.14 | -0.04362588 | 0.03001051 | TIME_EXIT |
| 60 | 2026-08-28 04:12:48.732618Z | ETH_USDT | SHORT | 0.012049 | 2478.81 | 2488.30 | -0.14426936 | 0.02992435 | STOP_LOSS |
| 66 | 2026-08-28 04:28:13.983549Z | ETH_USDT | SHORT | 0.012049 | 2486.96 | 2489.79 | -0.06408110 | 0.02998243 | TIME_EXIT |
| 72 | 2026-08-28 08:00:44.253635Z | SNXX_USDT | SHORT | 2.434669 | 12.281 | 12.332 | -0.15413037 | 0.02996225 | STOP_LOSS |
| 78 | 2026-08-28 08:01:00.560518Z | MRVL_USDT | LONG | 0.135863 | 222.26 | 221.32 | -0.15784428 | 0.03013306 | STOP_LOSS |
| 86 | 2026-08-28 08:03:22.185183Z | ENA_USDT | LONG | 100.000000 | 0.16775 | 0.16903 | 0.19913762 | 0.01506037 | TAKE_PROFIT |
| 90 | 2026-08-30 05:30:50.704000Z | MSTRX_USDT | LONG | 0.217975 | 138.000000 | 129.454104 | -1.89194084 | 0.02914916 | RECOVERY_FLATTEN |
| 98 | 2026-08-30 05:42:08.591159Z | BTR_USDT | SHORT | 100.000000 | 0.18234 | 0.18324 | -0.17666668 | 0.01493456 | STOP_LOSS |
| 104 | 2026-08-30 05:42:26.387693Z | PROM_USDT | LONG | 4.236689 | 7.1244 | 7.0646 | -0.28341119 | 0.03005719 | STOP_LOSS |
| 110 | 2026-08-30 05:48:13.095581Z | UNI_USDT | SHORT | 6.127450 | 4.867 | 4.885 | -0.14017155 | 0.02987745 | TIME_EXIT |
| 118 | 2026-08-30 05:52:12.214709Z | 4_USDT | SHORT | 100.000000 | 0.01863 | 0.01875 | -0.22639478 | 0.01439492 | STOP_LOSS |
| 126 | 2026-08-30 06:07:44.584983Z | 4_USDT | LONG | 100.000000 | 0.01855 | 0.01846 | -0.18004301 | 0.01418269 | STOP_LOSS |
| 134 | 2026-08-30 06:08:20.570278Z | BTR_USDT | SHORT | 100.000000 | 0.17840 | 0.17913 | -0.14821418 | 0.01460708 | STOP_LOSS |
| 140 | 2026-08-30 06:09:50.238105Z | PROM_USDT | SHORT | 4.236689 | 7.1426 | 7.1724 | -0.15657743 | 0.03032410 | STOP_LOSS |
| 148 | 2026-08-30 06:22:02.212677Z | 4_USDT | SHORT | 100.000000 | 0.01835 | 0.01847 | -0.22596467 | 0.01417987 | STOP_LOSS |
| 154 | 2026-08-30 06:22:49.962133Z | PROM_USDT | SHORT | 4.236689 | 7.0446 | 7.0724 | -0.14768462 | 0.02990467 | STOP_LOSS |
| 160 | 2026-08-30 06:28:36.923637Z | UNI_USDT | LONG | 6.127450 | 4.901 | 4.913 | 0.04346200 | 0.03006740 | TIME_EXIT |
| 166 | 2026-08-30 06:53:29.970806Z | PROM_USDT | SHORT | 4.236689 | 7.1146 | 7.1484 | -0.17341403 | 0.03021394 | STOP_LOSS |
| 174 | 2026-08-30 06:55:05.494023Z | 龙虾_USDT | SHORT | 100.000000 | 0.074188 | 0.074512 | -0.16531288 | 0.01542478 | STOP_LOSS |
| 180 | 2026-08-30 07:37:55.112131Z | PROM_USDT | SHORT | 4.236689 | 7.0736 | 7.1034 | -0.15628510 | 0.03003177 | STOP_LOSS |
| 188 | 2026-08-30 07:40:13.005869Z | BTR_USDT | LONG | 100.000000 | 0.18224 | 0.18122 | -0.19611461 | 0.01486543 | STOP_LOSS |
| 194 | 2026-08-30 07:45:36.036139Z | PROM_USDT | LONG | 4.236689 | 7.1384 | 7.1426 | -0.01245799 | 0.03025208 | TIME_EXIT |
| 200 | 2026-08-30 07:55:18.299614Z | UNI_USDT | SHORT | 6.127450 | 4.857 | 4.877 | -0.15237130 | 0.02982230 | STOP_LOSS |
| 208 | 2026-08-30 08:11:55.133881Z | 龙虾_USDT | SHORT | 100.000000 | 0.07359 | 0.07371 | -0.08047369 | 0.01526878 | TIME_EXIT |
| 216 | 2026-08-30 08:26:59.291061Z | BTR_USDT | SHORT | 100.000000 | 0.18266 | 0.18151 | 0.15861671 | 0.01489564 | TAKE_PROFIT |
| 224 | 2026-08-30 08:32:47.789230Z | 龙虾_USDT | SHORT | 100.000000 | 0.073065 | 0.073367 | -0.15572487 | 0.01518861 | STOP_LOSS |
| 232 | 2026-08-30 08:37:11.754731Z | BTR_USDT | LONG | 100.000000 | 0.18324 | 0.18438 | 0.15669807 | 0.01501576 | TAKE_PROFIT |
| 240 | 2026-08-30 08:53:05.649080Z | TUT_USDT | LONG | 861.870834 | 0.03505388397 | 0.03490311603 | -0.16008945 | 0.03014695 | STOP_LOSS |
| 248 | 2026-08-30 08:56:46.928700Z | 4_USDT | LONG | 1545.993300 | 0.01966593532 | 0.01958406468 | -0.15691157 | 0.03034012 | STOP_LOSS |
| 256 | 2026-08-30 08:58:10.493312Z | 龙虾_USDT | LONG | 432.052537 | 0.07198476855 | 0.07171223145 | -0.14879267 | 0.03104233 | STOP_LOSS |
| 264 | 2026-08-30 09:04:26.384374Z | TUT_USDT | SHORT | 861.870834 | 0.03463211603 | 0.03469988397 | -0.08828484 | 0.02987762 | TIME_EXIT |
| 272 | 2026-08-30 09:11:52.957730Z | 龙虾_USDT | SHORT | 432.052537 | 0.07363123145 | 0.07392576855 | -0.15913168 | 0.03187619 | STOP_LOSS |
| 278 | 2026-08-30 10:49:48.219446Z | HNT_USDT | SHORT | 50.293378 | 0.5943 | 0.5967 | -0.15065382 | 0.02994971 | STOP_LOSS |
| 284 | 2026-08-30 11:05:55.931670Z | UNI_USDT | LONG | 6.145022 | 4.965 | 4.973 | 0.01862556 | 0.03053462 | TIME_EXIT |
| 292 | 2026-08-30 11:11:38.796812Z | 龙虾_USDT | LONG | 418.462568 | 0.07279276103 | 0.07321723897 | 0.14707827 | 0.03054986 | TAKE_PROFIT |
| 298 | 2026-08-30 11:21:04.557167Z | PROM_USDT | LONG | 4.533776 | 6.7984 | 6.7986 | -0.02991611 | 0.03082287 | TIME_EXIT |
| 304 | 2026-08-30 11:32:20.123206Z | HNT_USDT | LONG | 50.293378 | 0.6087 | 0.6123 | 0.15035205 | 0.03070411 | TAKE_PROFIT |
| 312 | 2026-08-30 11:37:23.068835Z | 龙虾_USDT | SHORT | 418.462568 | 0.07183023897 | 0.07173376103 | 0.01033433 | 0.03003808 | TIME_EXIT |
| 318 | 2026-08-30 11:40:18.950061Z | HNT_USDT | LONG | 50.293378 | 0.6565 | 0.6535 | -0.18382229 | 0.03294216 | STOP_LOSS |
| 324 | 2026-08-30 11:48:11.411259Z | HNT_USDT | LONG | 50.293378 | 0.6899 | 0.6843 | -0.31619950 | 0.03455658 | STOP_LOSS |
| 332 | 2026-08-30 11:54:48.842531Z | 龙虾_USDT | SHORT | 418.462568 | 0.07119223897 | 0.07152476103 | -0.16900889 | 0.02986086 | STOP_LOSS |
| 340 | 2026-08-30 12:00:12.598994Z | TUT_USDT | LONG | 871.257224 | 0.03481588522 | 0.03434111478 | -0.44377395 | 0.03012677 | STOP_LOSS |
| 346 | 2026-08-30 12:04:07.835395Z | HNT_USDT | LONG | 50.293378 | 0.6788 | 0.6787 | -0.03916597 | 0.03413663 | MANUAL_FLATTEN |

## 4. PnL and cost analysis

### Retained ledger totals

| Metric | Value |
| --- | ---: |
| All closed records | 51 |
| Fully timestamped closed records | 45 |
| Legacy closed records | 6 |
| Positive trades | 8 |
| Negative trades | 37 |
| Flat trades | 0 |
| Win rate | 17.7778% |
| Positive PnL total | 0.88430461 USDT |
| Negative PnL total | -7.44835871 USDT |
| Recorded realized PnL total | -6.56405410 USDT |
| Average win | 0.11053808 USDT |
| Average loss | -0.20130699 USDT |
| Profit factor | 0.11872476 |
| Expectancy/trade | -0.14586787 USDT |
| Close-record fees with fee fields | 1.18479453 USDT |
| All fill fees | 1.52895660 USDT |
| Recovery entry fee implied by checkpoint | approximately 0.01410888 USDT |
| Checkpoint accumulated fees | 1.54306548 USDT |

The fee totals are not interchangeable:

- close-record fees omit all six legacy close fees;
- fill fees include entry and exit fill fees and split fills;
- the recovery flatten has no corresponding normal fill pair, so its entry fee is only inferable from
  the checkpoint/close record;
- the checkpoint total is consistent with fill fees plus the recovery entry fee within rounding, but
  the close-record fee column alone is incomplete.

### Exit reasons

| Reason | Trades |
| --- | ---: |
| STOP_LOSS | 26 |
| TIME_EXIT | 12 |
| TAKE_PROFIT | 5 |
| RECOVERY_FLATTEN | 1 |
| MANUAL_FLATTEN | 1 |

The loss profile is dominated by stop losses: 26 of 51 retained trades. Take-profit trades are only
5 of 51. The recovery flatten is the single largest loss at -1.89194084 USDT and must be kept in all
risk reporting rather than excluded as an operational event.

### Symbol totals

| Symbol | Trades | Net PnL USDT | Fees in close records |
| --- | ---: | ---: | ---: |
| ETH_USDT | 5 | -0.42140990 | 0.14947521 |
| SNXX_USDT | 1 | -0.15413037 | 0.02996225 |
| MRVL_USDT | 1 | -0.15784428 | 0.03013306 |
| ENA_USDT | 1 | 0.19913762 | 0.01506037 |
| MSTRX_USDT | 1 | -1.89194084 | 0.02914916 |
| BTR_USDT | 5 | -0.20568069 | 0.07431847 |
| PROM_USDT | 7 | -0.95974647 | 0.21160662 |
| UNI_USDT | 4 | -0.23045529 | 0.12030177 |
| 4_USDT | 4 | -0.78931403 | 0.07309760 |
| 龙虾_USDT | 8 | -0.72103208 | 0.19924949 |
| TUT_USDT | 3 | -0.69214824 | 0.09015134 |
| HNT_USDT | 5 | -0.53948953 | 0.16228919 |

Only ENA_USDT is positive in the retained attributable ledger. No symbol is profitable enough to
support promotion, and the sample is too small to claim symbol-level robustness.

### Holding-time caveat

Timestamp matching yields 45 measurable holds, but the calculated average is approximately 3,766
seconds and the maximum is approximately 163,640 seconds. Those values are not credible for a
short-duration scalper because the event stream contains restart/recovery gaps and does not preserve
a robust position identifier. Holding-time reporting must be fixed before it is used for strategy
decisions.

## 5. State reconciliation

### Checkpoint at audit cutoff

`logs/paper-state.json`:

```json
{
  "schema_version": 2,
  "mode": "PAPER",
  "symbol": "MULTI",
  "balance_usdt": "292.99226546",
  "trades": 51,
  "fees_usdt": "1.5430654791597000",
  "position": null,
  "risk_day_utc": "2026-08-30",
  "day_start_equity_usdt": "299.0220726297597",
  "trades_at_day_start": 14,
  "peak_equity_usdt": "300",
  "risk_halted": true,
  "risk_halt_reason": "DAILY_LOSS"
}
```

Checkpoint implications:

- 300.00000000 - 292.99226546 = **7.00773454 USDT** cumulative reduction;
- daily-loss reference is 299.02207263 USDT;
- 299.02207263 - 292.99226546 = **6.02980717 USDT**, exceeding the configured 6 USDT daily halt;
- the checkpoint correctly records no open position and a sticky DAILY_LOSS halt for 2026-08-30.

### Live API at cutoff

`GET /api/state` at 2026-08-31T01:56:05.874977Z reported:

| Field | API value |
| --- | ---: |
| Trading state | `HALTED` |
| Engine | `SIMULATION_READY`, RiskEngine enabled |
| Gate data | `LIVE`, 0.8 s old, 1,344 ms latency |
| Equity | 300.00000000 USDT |
| Daily PnL | +7.00773454 USDT |
| Trades today | 0 |
| Open positions | 0 |
| Fees | 1.54306548 USDT |
| Modeled slippage | 0.61722619 USDT |
| Orders | 0 |
| Paper strategy | `PAUSED`, `WARMING_UP` |
| Alert | `Paper execution: pessimistic paper quote is crossed for PUMP_USDT` |
| Resume | not allowed |

### Mismatch and likely root cause

The API's 300 USDT equity conflicts with the checkpoint's 292.99226546 USDT. Its +7.00773454 daily
P&L is the exact absolute difference between 300 and the checkpoint balance, so the API is treating the
checkpoint loss as a positive daily change or has initialized the current engine/account from the
configured 300 USDT baseline rather than the persisted balance.

Source review shows the risk/accounting boundary is fragile:

- `_load_state()` loads persisted `balance_usdt` into `self._initial_balance`;
- `_initialize()` creates the Nautilus account from that value;
- `_portfolio()` mutates the daily risk window based on its supplied `equity`;
- `_inactive_snapshot()` and live engine snapshots use different paths;
- persistence is skipped or interrupted when processing returns early or a pessimistic quote is
  rejected;
- no startup invariant compares the restored checkpoint balance, engine account balance, event-ledger
  realized PnL, and API portfolio before paper operation is exposed.

This is a **confirmed reconciliation failure**, but the exact branch causing the 300 USDT API value
must be reproduced under a controlled restart before patching. The safe response is to keep entries
halted and add a startup reconciliation gate.

## 6. Runtime, restart, and watchdog chronology

Observed operational counts:

| Log/source | Count or fact |
| --- | ---: |
| Dashboard log lines | 10,736 |
| Dashboard starts | 55 |
| Paper tournament initializations | 21 |
| Gate public feed failures | 4 |
| Health-check lines | 58 |
| Healthy watchdog records | 33 |
| Unhealthy watchdog records | 1 |
| Service healthy but trading inactive | 7 |
| Safe PAPER resumes | 16 |
| TradingView unavailable warnings | 2 |
| TradingView sidecar recoveries | 2 |
| Dashboard restarts by watchdog | 0 |
| Engine lifecycle smoke records | 44 |

Key sequence:

1. 2026-08-28: repeated dashboard starts and paper-universe rebuilds occurred while the first
   timestamped trade series was recorded.
2. 2026-08-28 07:50:51Z: watchdog detected stale data and HALTED trading; recovery later restored
   ACTIVE after fresh data.
3. 2026-08-30 05:29Z: TradingView became unavailable; the sidecar was restarted. The bot remained
   operationally separate.
4. 2026-08-30 05:29–05:36Z: a persisted MSTRX paper position required recovery flatten; the recovery
   event at line 90 closed it for -1.89194084 USDT.
5. 2026-08-30 05:34Z: a pessimistic paper quote was crossed for UNI_USDT; entries were halted until
   recovery.
6. 2026-08-30 09:21Z: the watchdog recorded the paper risk limit reached.
7. 2026-08-30 12:13Z: the DAILY_LOSS halt was explicitly logged.
8. 2026-08-31 08:55 WIB: the dashboard restarted and rebuilt a fresh eight-symbol universe. The
   first current snapshot later halted again because the pessimistic quote was crossed for PUMP_USDT.

The watchdog correctly avoided silently restarting the dashboard in this retained period, but it did
not prevent the accounting/API mismatch from being exposed after restart.

## 7. Confirmed flaws and corrective actions

### P0 — Restore accounting integrity before any further paper entries

**Evidence:** checkpoint balance 292.99226546, 51 trades, DAILY_LOSS versus API equity 300.00,
`trades_today=0`, and +7.00773454 daily PnL at the same audit cutoff.

**Likely cause:** persisted balance, Nautilus account balance, daily-window reset, and API snapshot are
not checked as one atomic invariant across restart/day rollover; crossed-quote early failure can also
prevent a corrective checkpoint.

**Smallest safe fix:** on startup, load the checkpoint, initialize Nautilus from the persisted balance,
then require exact equality (within documented decimal tolerance) among checkpoint balance, engine
balance, reconstructed ledger, and API snapshot. If any mismatch exists, set `STATE_INVALID`, keep
entries disabled, and emit a structured reconciliation event. Do not auto-resume.

**Files/functions:** `autotrade/paper.py` `_load_state`, `_initialize`, `_portfolio`, `_persist_state`,
`autotrade/dashboard.py` `DashboardState.snapshot` and startup orchestration.

### P0 — Preserve attributable history

**Evidence:** six close records at lines 6–36 lack identity/time/cost fields; 36 legacy events cannot
be assigned to symbols or holding periods.

**Smallest safe fix:** version the event schema and reject/flag writes without `timestamp_utc`, symbol,
side, position ID, order ID, fee, and prices. Preserve legacy rows as `unattributed` rather than
pretending they are complete. Add a session/run ID and monotonic sequence to every event.

**Files/functions:** `autotrade/paper.py` `_write_events`, `_trade_record`, strategy event creation.

### P0 — Stop treating five-second REST as scalping evidence

**Evidence:** no WebSocket/L2/trade capture exists; four Gate feed failures and multiple crossed
pessimistic quotes are logged; the simulator uses synthetic fixed sizes and no measured latency.

**Smallest safe fix:** keep this challenger in observation-only mode and build Gate WebSocket L2/trade
capture with sequence recovery, raw persistence, exchange/local timestamps, and replay before tuning
entry/exit parameters.

### P1 — Fix pessimistic quote construction

**Evidence:** the current runtime halts on `pessimistic paper quote is crossed for PUMP_USDT`; the
   quote applies slippage and then rounds both sides to instrument precision.

**Likely cause:** low-priced instruments lose the spread during adverse-slippage rounding, converting
valid raw quotes into invalid crossed quotes.

**Smallest safe fix:** validate raw bid/ask before rounding, use instrument increments that preserve a
strict positive spread, and reject only the affected symbol rather than halting the entire portfolio
unless the portfolio integrity invariant is actually broken. Add low-price and precision tests.

**Files/functions:** `autotrade/paper.py` `_quote`, `PaperTrader.process`, `tests/test_paper.py`.

### P1 — Correct daily-risk rollover and checkpoint timing

**Evidence:** checkpoint remains on `risk_day_utc=2026-08-30` after the UTC day changed, while the API
reports a new-day positive daily PnL and remains HALTED.

**Smallest safe fix:** persist the new risk day and day-start values transactionally before returning
the first post-midnight snapshot; define whether daily-loss halts reset only after explicit review or
after a clean restart, then test both paths. Do not use a stale checkpoint to calculate positive PnL.

### P1 — Add full execution audit fields

**Evidence:** 143 fills for 51 closes, split fills are present, but there are no durable order IDs,
position IDs, latency values, funding, queue, or fill-cause fields.

**Smallest safe fix:** add event schema fields and a deterministic order/position lifecycle before
implementing realistic maker or partial-fill logic.

### P2 — Operational and documentation cleanup

- rotate/archive `paper-events.jsonl` and add a free-disk watchdog threshold;
- add the documented `stop_bot.bat` or remove it from the runbook;
- reconcile roadmap test counts and the TradingView default contradiction;
- remove/track Nautilus `Timestamp.utcnow` deprecation warnings;
- create an immutable Git baseline before the next audit.

## 8. Strategy decision

The retained sample does not support continued unrestricted paper trading:

- 51 closed records;
- 8 wins and 37 losses;
- 17.78% win rate;
- 0.1187 profit factor;
- -6.5641 USDT recorded ledger PnL, excluding unrecoverable fee fields from six legacy closes;
- one recovery flatten caused -1.8919 USDT;
- stop losses outnumber take profits by more than five to one.

**Decision:** pause entries until P0 accounting/recovery and event-schema fixes are complete. After the
fix, restart with a fresh, clearly versioned paper run. Do not optimize parameters against this mixed
legacy/restarted dataset. Once data capture and simulator realism exist, rerun the same strategy as a
baseline and compare it against simple no-trade and non-strategy controls.

## 9. Verification performed

Read-only audit calculations were independently recomputed from the event file. The current build
checks previously passed on the then-current files:

- `check.bat`: 39 Python tests passed, Ruff passed, mypy passed;
- `check_ui.bat`: 3/3 Chromium Playwright tests passed;
- no malformed event lines were found in the current 346-line ledger;
- current API was reachable and returned PAPER mode with RiskEngine enabled.

Those software checks do not invalidate the accounting mismatch; they show that the mismatch is an
untested cross-process/restart behavior rather than a currently covered unit-test failure.

## 10. Next action

**Do not resume entries. Reproduce the restart mismatch with a copied test fixture, add the startup
reconciliation gate, and commit that fix plus the schema tests before collecting another trading
sample.**
