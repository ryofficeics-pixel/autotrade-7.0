# PAPER incident report — restart recovery flatten

## Verdict

The laptop/process interruption did not create a strategy win. It left an already-open
`LSK_USDT` PAPER long unmanaged for about 1 hour 47 minutes; after restart, the system
correctly restored it as a recovery position and required an explicit fresh-market
flatten. That recovery flatten realized **+5.08953774 USDT** (**+16.3058%** on its
approximately 31.21-USDT entry notional; **+1.6965%** of the 300-USDT starting equity).

This is a valid record in the current PAPER ledger, but it is not a normal V2 exit, not
an exchange fill, and not evidence that shutting down improves returns. Excluding that
one recovery event, the 15 September closed-trade sample is **-2.08830097 USDT**.

Remain PAPER-only. Do not turn the shutdown/restart pattern into a holding or exit rule.

## Scope and evidence cutoff

This is a read-only snapshot taken on **15 September 2026 at 13:42:40 WIB**
(06:42:40 UTC). It covers the active schema-v3 PAPER run
`aa89e54a-e88a-4732-b254-615a58f1721a`, its event ledger, checkpoint, dashboard log,
and a live local-state observation. All times below are WIB unless labelled UTC.

The ledger scan at the cutoff completed successfully: 2,385 contiguous events, no legacy
events, 197 closed/recovery trades, and one currently open position. The checkpoint and
ledger both report `PAPER`; no live order or account claim is made here.

## Incident chronology

| Time | Evidence | Meaning |
|---|---|---|
| 11:37:52 | Ledger position opened | V2 opened a `LSK_USDT` long: 84.245998 units at 0.3705. |
| 11:37:59 | Last dashboard state request before interruption | The log then has no dashboard activity for 1h45m21s. |
| 13:23:20 | Dashboard started | Restart occurred; log does not identify whether this was a laptop power-off, a process crash, or another host-level interruption. |
| 13:25:16 | `POST /api/control/flatten` | User explicitly requested recovery flatten after fresh public market data became available. |
| 06:25:15.978 UTC / 13:25:15.978 WIB | Ledger sequence 2365 | `recovery_flatten` closed the persisted LSK long at 0.43131372 with reason `RECOVERY_FLATTEN`. |
| 13:25:19 | `POST /api/control/resume` | Resume occurred only after flatten. A new engine session was initialized. |
| 13:37:18 | New `FF_USDT` short opened | Normal PAPER trading resumed. |
| 13:38:33 | `POST /api/control/flatten` | The FF short was manually flattened for -0.03884634 USDT. |
| 13:41:25 | New `PONS_USDT` short opened | This position remained open at the cutoff. |

The elapsed time from LSK entry to recovery flatten is approximately 1h47m24s. The
recovery event has no `holding_time_ms` in the ledger, so the elapsed value above is
computed from the entry and recovery event timestamps.

## The recovery-profit event

| Field | Recorded value |
|---|---:|
| Symbol / side | `LSK_USDT` long |
| Entry | 84.245998 at 0.3705 |
| Recovery exit | 0.43131372 |
| Exit reason | `RECOVERY_FLATTEN` |
| Ledger realized PnL | +5.0895377361 USDT |
| Ledger PnL percentage | +16.30575254% |
| Recorded fee | 0.0337747974 USDT |
| Ledger sequence | 2365, enclosed by committed transition 2364–2366 |

The exit logic used the fresh public-Gate **bid** less configured slippage for a long,
then applied the modeled taker fee. The execution model reports observed spread and
modeled fees/slippage, but does **not** model decision latency, submit latency, fill
latency, fill ratio, adverse selection, funding, or queue position. Therefore the result
is a simulator realization at the restart-time quote, not proof a real position could
have exited at that price and size.

## 15 September economics through the cutoff

### Closed-trade decomposition

| Scope | Closed events | Realized PnL |
|---|---:|---:|
| Normal strategy exits | 34 | -2.04945463 USDT |
| Manual flatten | 1 | -0.03884634 USDT |
| All non-recovery position closes | 35 | -2.08830097 USDT |
| One recovery flatten | 1 | +5.08953774 USDT |
| All closed/recovery events | 36 | +3.00123677 USDT |
| Dashboard daily PnL with the new PONS entry fee | 36 closed/recovery; 1 open | +2.98645754 USDT |

The 0.01477923-USDT difference between closed-event PnL and dashboard daily PnL equals
the recorded entry fee on the currently open `PONS_USDT` short. It is not realized exit
profit or loss.

### Exit reasons

| Reason | Count | Realized PnL |
|---|---:|---:|
| Take profit | 11 | +2.63 USDT |
| Stop loss | 18 | -4.38 USDT |
| Time exit | 5 | -0.30 USDT |
| Manual flatten | 1 | -0.04 USDT |
| Recovery flatten | 1 | +5.09 USDT |

The 35 non-recovery closes had 11 winners and 24 losers, but one loss was a manual
flatten and is not normal strategy alpha. The recovery event is the largest positive
outlier by a wide margin; without it, the day is negative. The two largest normal losses
were `CAP_USDT` stop losses of -0.61534360 and -0.61119160 USDT.

### Symbol concentration

| Symbol | Closed/recovery events | Realized PnL |
|---|---:|---:|
| LSK_USDT | 6 | +5.15 USDT |
| CAP_USDT | 12 | -0.52 USDT |
| 龙虾_USDT | 13 | -1.27 USDT |
| XRP_USDT | 2 | -0.21 USDT |
| ZEC_USDT | 1 | -0.09 USDT |
| FF_USDT | 1 | -0.04 USDT |
| SOXL_USDT | 1 | -0.03 USDT |

Almost all positive daily result comes from the recovery LSK event. Treating the LSK
symbol total as normal strategy performance would be misleading.

## Restart-safety assessment

### What worked

- The durable checkpoint preserved the open LSK identity, quantity, entry price, and
  entry fee rather than inventing a replacement position after restart.
- The restart did not automatically resume entries from a recovered position.
- Recovery demanded an explicit flatten with fresh market data before the later manual
  resume. This is the required fail-closed path.
- The recovery mutation is bracketed by an intent and committed transition, and the
  ledger remained reconcilable after the restart.

### What did not work operationally

- The strategy was unavailable to manage the open position for roughly 107 minutes.
  Its take-profit, stop-loss, and time-exit rules were not running during that interval.
- A favorable move happened by chance. The same exposure could have moved through the
  stop or gap limit while the machine was off. The observed payoff is survivorship bias,
  not a viable operational policy.
- The system has no evidence in this incident of an independent always-on supervisor or
  external emergency-exit mechanism. A laptop-hosted execution process has an obvious
  single point of failure.
- The recovery close used a new REST quote after restart. It has no historical
  intrainterruption path, order-book depth at the actual exit, or executable exchange-fill
  evidence.

## Current status at the cutoff

| Control | Observed state |
|---|---|
| Mode / venue | `PAPER` / `GATE` |
| Engine | `SIMULATION_READY`, risk engine enabled |
| Data | `LIVE`, public Gate REST, age about 0.1 seconds |
| Accounting / recovery | `VALID` / no recovery blocker |
| Risk | `OK`, UTC-day drawdown about 0.0266% |
| Equity | 294.19068254 USDT; -5.80931746 USDT versus 300-USDT run start |
| Open position | `PONS_USDT` short, 48.520135 at 0.6092; current quote 0.6093 |
| Controls | Pause and flatten available; resume unavailable because strategy is active |

The process is currently operational, but that does not validate profitability or
real-market readiness.

## Decision and next actions

**P0 — Do not change strategy rules from this event.** Classify it as
`outage-held / recovery-flatten`, exclude it from normal-exit expectancy, and keep V2
PAPER-only.

**P0 — Treat host loss during an open position as a critical operational risk.** Before
any broader PAPER experiment, define an explicit maximum outage exposure and a tested
restart runbook. A clean shutdown should flatten first or deliberately retain and label
the exposure; an uncontrolled shutdown must never be treated as a return source.

**P1 — Add recovery-outage analytics.** Persist last successful process heartbeat,
restart detected time, outage duration, and an explicit `OUTAGE_HELD` tag. Report this
separately from strategy exits. This is an instrumentation change, not an alpha change.

**P1 — Reconcile and evaluate normal trades only.** Run chronological cost-aware replay
and a holdout/walk-forward evaluation after excluding recovery/manual outcomes. The
available normal sample is negative and cannot support promotion.

**P2 — Remove the laptop as the single execution host only after PAPER soak evidence.**
An always-on, monitored host is the relevant resilience improvement; do not automate
restart-to-trade until recovery behavior has been fault-injection tested.

## Evidence references

- Event ledger: `data/runs/aa89e54a-e88a-4732-b254-615a58f1721a/events.jsonl`.
  SHA-256 of the immutable 2,385-line prefix at the report cutoff:
  `FF1C6BD971F13AD14EE78C7E55DAFF92DEC07069C9161C63B6C6746365561F46`.
- Current checkpoint: `logs/paper-state.json`.
- Service chronology: `logs/dashboard.log` (lines around 34161–34247 at this snapshot).
- Execution/recovery implementation: `autotrade/paper.py` (`_flatten_recovery`).
- Prior period audit: `docs/AUDIT_REPORT_2026-09-12_TO_2026-09-15.md`.

## Limits

This report is not an exchange-account statement, a live-fill reconciliation, a proof of
profitability, or authorization for live trading. It is a local PAPER-simulator incident
analysis based on public REST data and modeled execution costs.
