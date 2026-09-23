# AUTOTRADE 7.0 Full Strategy, AMA Control, and Loss Audit

## Executive Summary

AUTOTRADE 7.0 is operationally healthy but does not have a strategy that is eligible to trade.
The current PAPER baseline has failed its configured evidence gate. Across 427 normal strategy
closes, it lost 24.84421854 USDT, produced a 0.5062 profit factor, and lost in both chronological
halves. Its gross price PnL was already negative before fees. This is a strategy failure, not a
dashboard, accounting, market-data, or restart failure.

The independent XAU KAMA20 control has now collected a meaningful raw sample, but the result is also
negative. Its 654 raw closes lost 16.54116247 USDT under the baseline execution profile and only
8 of 654 closes were profitable. The filtered KAMA family has one losing close, which is insufficient
evidence. Entry V3 remains execution-disabled and had accepted zero of 15,713 candidates at the live
cutoff. No current challenger is promotion-eligible.

The correct current state is therefore `HALTED`, flat, unarmed, and PAPER-only. An ordinary Resume
request was tested through the local control API and correctly returned HTTP 409. Three later polling
cycles remained flat and halted. Re-enabling the same strategy would restore a known negative
expectancy process, not fix it.

The research infrastructure has materially improved: KAMA evidence is append-only and hash-chained,
rejected candidates receive later counterfactual outcomes, execution profiles are explicit, and the
failed main strategy now halts durably. The remaining critical gap is a same-timeline chronological
replay and walk-forward comparison. Until that exists, the system cannot establish whether any
strategy has robust tradable edge.

## Scope and Fixed Cutoffs

- Application: AUTOTRADE 7.0.
- Execution boundary: Phase 1 PAPER simulator only.
- Venue data source: Gate public market data.
- Trade-ledger cutoff: `2026-09-23 03:08:23 UTC` (`10:08:23 WIB`).
- Live runtime cutoff: `2026-09-23 06:39:13 UTC` (`13:39:13 WIB`).
- KAMA observation at runtime cutoff: `2026-09-23 06:39:07.868 UTC`.
- Run ID: `aa89e54a-e88a-4732-b254-615a58f1721a`.
- Session ID: `ba793a0a-72c0-428a-a9ee-f26b646db359`.
- Current checkout and `origin/main`: `345f8f90d68b89905b41e20650ecf43f028e87e8`.
- Runtime run metadata commit: `fbed164bc3ac348390aeeb645d43f92a8a429107`.
- Runtime config hash: `5fb18e8f66ebf188560fc50d08f8a3f8456ac267d3254c63ad07d06bebc619ff`.
- KAMA parameter-set ID: `f13b98ff9225f0be`.

The trade ledger stopped changing when the final baseline trade closed. KAMA research evidence
continues to append while the executable strategy remains halted. KAMA figures in this report are
therefore fixed to the stated runtime cutoff, not to later file growth.

## Evidence Register

| Evidence | Fixed evidence used | Purpose | Boundary |
|---|---|---|---|
| Run ledger | 5,333 lines, 5,882,151 bytes, SHA-256 `2C848B66BAADF1A243E3AEFA047A956EE2EFCE4A367AAA359C04B6B2771DF374` | Reconstruct fills, closes, fees, classifications, and chronology | PAPER fills, not exchange fills |
| KAMA V2 evidence prefix | 45,401 lines through 49,374,448 bytes, SHA-256 `00577916FD7CD5371A5272F5A0B8A342537462DB824BC9E5C65D9763CDD4000D` | Verify append-only control observations, decisions, and outcomes | Prefix captured after the live cutoff; later records continue to append |
| `/api/state` | Live snapshot at `06:39:13 UTC` | Operational, accounting, risk, control, and research state | Point-in-time state |
| `autotrade.profitability_cli` | Full static run ledger | Deterministic normal/recovery/manual attribution and chronological 60/20/20 split | Observed PAPER run, not causal market replay |
| Generated research reports | Latest atomic Markdown and JSON derivatives | KAMA health, quality, counterfactual, execution, and walk-forward status | Derived from KAMA V2 evidence |
| Source and Git | Checkout `345f8f9`, `git diff --check`, `git fsck --no-dangling` | Trace implementation and repository integrity | Run metadata predates current checkout |
| Automated validation | 125 Python tests, Ruff, mypy, 5 Playwright tests | Verify software behavior and failure paths | Does not prove profitability or live readiness |

Recovery and manual closes are separated from normal-strategy evidence. This prevents outage-held
price movement and operator actions from being reported as strategy alpha.

## Current Operational State

| Item | State at cutoff | Interpretation |
|---|---|---|
| Mode / venue | `PAPER` / `GATE` | No live-order authorization |
| Trading state | `HALTED` | Correctly blocked by strategy evidence |
| Strategy | `PAUSED`, armed=`false` | Cannot open new positions |
| Open positions | 0 | Flat |
| Accounting | `VALID` | Ledger and checkpoint reconcile |
| Data | `LIVE`, age 5.6 seconds | Public feed was fresh at capture |
| Risk | `STRATEGY_EVIDENCE_FAILED` | Sole trading blocker |
| Recovery | `BLOCKED`, code `RISK_HALT` | No accounting repair or flatten is required |
| Resume control | disabled | Ordinary Resume cannot clear the failed evidence |
| Market scope | `WIDE_CRYPTO`, no pending switch | Scope is stable |
| Dashboard listener | one process on `127.0.0.1:8767` | Local-only control plane |
| Engine | `SIMULATION_READY` | Operational readiness only |

`health_check.bat --once` reported the service healthy but trading inactive because the active PAPER
strategy failed the evidence gate. That warning is expected. It is not a service outage.

## Financial Baseline

| Measure | Normal strategy | Recovery | Manual | Entire run |
|---|---:|---:|---:|---:|
| Closed trades | 427 | 5 | 4 | 436 |
| Wins / losses | 141 / 286 | 3 / 2 | 1 / 3 | 145 / 291 |
| Gross price PnL | -12.17927339 | +5.09810319 | -0.06721251 | -7.14838272 USDT |
| Fees | 12.66494518 | 0.15362079 | 0.11961754 | 12.93818351 USDT |
| Net PnL | -24.84421854 | +4.94448239 | -0.18683006 | -20.08656621 USDT |
| Expectancy/trade | -0.05818318 | +0.98889648 | -0.04670752 | -0.04607011 USDT |
| Profit factor | 0.5062 | 5.8899 | 0.0904 | 0.6102 |
| Average hold | 110.16 seconds | 115,469.32 seconds | 143.17 seconds | 643.30 seconds |

Starting equity was 300 USDT. Current equity was 279.91343380 USDT, a total accounting decline of
20.08656621 USDT or 6.70%. Normal strategy drawdown reached 24.96912057 USDT or 8.32%. The entire-run
loss appears smaller only because recovery closes contributed 4.94448239 USDT. Recovery positions
were held for roughly 32 hours on average and are not evidence for the normal entry strategy.

## Why the System Keeps Losing Money

### 1. The selected price moves are negative before fees

Normal gross price PnL was `-12.17927339 USDT`. Fees then removed another `12.66494518 USDT`.
Reducing fees would lower the damage, but it would not make the current signal profitable because the
pre-fee result is already negative.

### 2. The win rate cannot support the realized payoff

The average winner was `+0.18062719 USDT` and the average loser was `-0.17591836 USDT`. That realized
payoff requires a 49.34% break-even win rate. The observed win rate was only 33.02%, a deficit of
16.32 percentage points.

### 3. Stop losses overwhelm profitable exits

| Exit | Trades | Net PnL | Average result |
|---|---:|---:|---:|
| `STOP_LOSS` | 244 | -47.89401767 USDT | -0.19628696 USDT |
| `TAKE_PROFIT` | 125 | +24.74024555 USDT | +0.19792196 USDT |
| `TIME_EXIT` | 58 | -1.69044642 USDT | -0.02914563 USDT |

Take-profit gains recover only 51.7% of stop-loss damage. Time exits add another loss.

### 4. Turnover converts weak selection into certain costs

The normal strategy turned over 25,329.8894 USDT in 427 closes. It averaged approximately 13.26
normal trades per active hour in the live dashboard calculation. The strategy does not need more
trades. It needs fewer, independently validated entries with positive gross edge.

### 5. Every holding-time group loses

| Holding time | Trades | Net PnL | Profit factor |
|---|---:|---:|---:|
| Under 1 minute | 199 | -14.43863799 USDT | 0.4876 |
| 1 to 5 minutes | 167 | -8.54910416 USDT | 0.5600 |
| Over 5 minutes | 61 | -1.85647639 USDT | 0.3141 |

Extending or shortening the existing exits has no descriptive support as a standalone repair.

### 6. Recorded confidence is not calibrated

All recorded confidence-score buckets are negative. The largest bucket, 100% to 109%, contained 347
trades and lost 19.91789329 USDT. Higher reported confidence did not identify higher realized edge.

### 7. Chronological evidence rejects a temporary bad-luck explanation

| Segment | Trades | Net PnL | PF | Expectancy | Bootstrap 95% expectancy interval |
|---|---:|---:|---:|---:|---:|
| Selection | 256 | -15.53707519 | 0.4901 | -0.06069170 | [-0.08322674, -0.03744655] |
| Validation | 85 | -4.03418966 | 0.5583 | -0.04746105 | [-0.08731916, -0.00439484] |
| Holdout | 86 | -5.27295369 | 0.5075 | -0.06131342 | [-0.11097421, -0.01571679] |
| Holdout, doubled cost | 86 | -7.85353595 | 0.3685 | -0.09132019 | [-0.14099866, -0.04572338] |

Every segment loses and every interval remains below zero. Both configured chronological halves also
failed: the first 213 trades lost 11.40415498 USDT at PF 0.5329; the final 214 lost 13.44006356 USDT
at PF 0.4811.

### 8. The execution model is optimistic relative to real trading

The PAPER model observes spread and models fees and slippage. It does not model decision, submit, or
fill latency; fill ratio; adverse selection; funding; or queue position. These omissions cannot be
used to rescue a result that is already materially negative under the simpler model.

## Root Causes

1. `REST_MOMENTUM_TOURNAMENT_V2` does not have positive realized gross selection edge.
2. Its confidence score does not rank realized outcomes reliably.
3. Its realized one-for-one payoff needs a win rate the signal does not achieve.
4. High turnover makes modeled fees a second loss engine.
5. Historical entries lack the continuous candidate set and market path needed to test causal
   alternatives such as edge decay, churn suppression, or partial runners after the fact.
6. The executable baseline and KAMA control are not evaluated on the same immutable XAU timeline.
7. No KAMA chronological holdout or walk-forward evaluation has run.
8. Entry V3 is collecting data but has produced no accepted candidates or trade outcomes.

## Architecture Before This Improvement Cycle

- NautilusTrader owned PAPER execution, risk, accounting, checkpointing, and recovery.
- `MarketScopeController` selected `WIDE_CRYPTO` or `XAU_ONLY` durably.
- The REST momentum tournament was the executable PAPER strategy.
- XAU logic used `XAU_USDT` with `XAUT_USDT` and `PAXG_USDT` as confirmation references.
- Entry V3 consumed separate WebSocket evidence with execution disabled.
- The dashboard was a read/control plane, not the trading engine.
- No independent same-timeline adaptive-moving-average control existed.
- A negative strategy could remain eligible until another operational or risk limit halted it.

## Architecture After This Improvement Cycle

- `AmaControlEngine` receives the retained XAU and reference observations in both scopes.
- KAMA 10/20/50, raw decisions, filtered decisions, reference state, quality reasons, shadow outcomes,
  and later counterfactuals are persisted in a separate hash-chained V2 stream.
- The KAMA control owns no order route, sizing authority, or Nautilus execution engine.
- Configuration and construction reject AMA execution enablement.
- Entry V3 remains a separate execution-disabled WebSocket shadow.
- The executable baseline now evaluates normal-strategy evidence before new entries.
- `STRATEGY_EVIDENCE_FAILED` persists across restart and ordinary Resume cannot clear it.
- The dashboard exposes strategy comparison and evidence state without becoming execution authority.

## Files Modified

Commit `223191d` introduced the independent KAMA control and modified:

- `.gitignore`, `README.md`, and `config/paper.toml`.
- `autotrade/ama_control.py`, `autotrade/config.py`, `autotrade/dashboard.py`, and
  `autotrade/paper.py`.
- `dashboard/app.js`, `dashboard/index.html`, and `dashboard/styles.css`.
- `docs/ARCHITECTURE.md`, `docs/BACKTEST_VALIDATION.md`, `docs/DASHBOARD_UI.md`,
  `docs/DATA_SPEC.md`, and `docs/AUTOTRADE_7_NEXT_CYCLE_BASELINE.md`.
- `tests/test_ama_control.py`, `tests/test_config.py`, and `tests/e2e/dashboard.spec.js`.

Commit `345f8f9` introduced the strategy-evidence halt and modified:

- `autotrade/config.py`, `autotrade/dashboard.py`, `autotrade/paper.py`, and `config/paper.toml`.
- `docs/RISK_MANAGEMENT.md`, `docs/TRADING_SPEC.md`, and
  `docs/AUDIT_REPORT_2026-09-23_LOSS_ROOT_CAUSE_AND_REMEDIATION.md`.
- `tests/test_config.py` and `tests/test_paper.py`.

This report adds no execution code and makes no runtime state change.

## Migrations

- No ledger, fill, checkpoint, position, or market-scope history was rewritten.
- Pre-validation KAMA V1 evidence remains preserved and excluded from V2 results.
- KAMA V2 starts and verifies its own append-only hash chain.
- `STRATEGY_EVIDENCE_FAILED` uses the existing persisted risk-state mechanism.
- No new PAPER run was created to erase or relabel the failed sample.
- Current run metadata still reports the older run-start commit. This limits immutable experiment
  claims and is disclosed rather than silently corrected.

## AMA Implementation

The control calculates Kaufman's Adaptive Moving Average incrementally for periods 10, 20, and 50.
It does not substitute a simple moving average. Parameters are validated and identified by the
stable parameter-set ID `f13b98ff9225f0be`.

Gate receive time is used as the observation clock because the retained REST ticker row does not
provide the required exchange event timestamp. Volatility is a quote-step ATR proxy, not candle true
range. Both limitations are visible in the evidence model.

## AMA10/20/50 Monitor

The monitor classifies `STRONG_UPTREND`, `UPTREND`, `STRONG_DOWNTREND`, `DOWNTREND`, `CHOP`,
`TRANSITION`, and `UNKNOWN`. At the fixed cutoff it had 6,818 observations, classified `UPTREND`,
produced a raw `LONG`, and retained filtered `WAIT` because the regime was not yet persistent.

## AMA Raw Control

The raw family enters LONG only when price is above KAMA20 and KAMA20 slope is positive. It enters
SHORT only for the symmetric negative condition. It uses no filtered quality permission and never
routes to orders.

The raw sample is now large enough to reject it under the current execution assumptions:

- 654 closes, 8 wins, and 646 losses.
- Gross modeled PnL before execution-profile costs: `+0.15840270 USDT`.
- Baseline costs: `16.69956517 USDT`.
- Baseline net: `-16.54116247 USDT`.
- Baseline expectancy: `-0.02529230 USDT/trade`.
- Baseline PF: `0.0108`.
- Maximum drawdown: `16.54116247 USDT`.
- LONG: 327 trades, `-8.34453927 USDT`.
- SHORT: 327 trades, `-8.19662320 USDT`.

Every reported regime is negative after baseline costs. Raw KAMA does not provide a replacement
strategy.

## AMA Filtered Control

The filtered family applies readiness, freshness, risk, spread, liquidity, reference, regime,
persistence, slope, hysteresis, extension, net-edge, confidence, and quarantine checks. At cutoff:

- 2,691 candidates were evaluated.
- 1 passed.
- 2,690 were rejected.
- The only closed filtered shadow lost `0.03089797 USDT`.
- Sample status was `INSUFFICIENT_EVIDENCE`.
- Promotion eligibility was `false`.

One close cannot establish either success or failure. The more important current observation is that
the filter passes 0.04% of candidates. This may be useful selectivity or excessive starvation. The
current evidence cannot distinguish the two.

## Hysteresis

The symmetric hysteresis band uses the maximum of the configured minimum, the volatility proxy
multiplied by its factor, and modeled baseline cost. At cutoff, `HYSTERESIS_BAND` rejected 382
candidates. Whether this reduces harmful whipsaw remains unproven because the filtered close sample
is one.

## Regime Logic

The largest filtered rejection source was `REGIME_MISMATCH` with 1,537 events, followed by
`REGIME_NOT_PERSISTENT` with 458. Raw KAMA performance was negative in every reported regime:

| Regime | Raw closes | Net PnL | Expectancy/trade |
|---|---:|---:|---:|
| CHOP | 504 | -12.78564957 | -0.02536835 USDT |
| DOWNTREND | 38 | -0.99195416 | -0.02610406 USDT |
| STRONG_DOWNTREND | 5 | -0.12182985 | -0.02436597 USDT |
| STRONG_UPTREND | 2 | -0.03783110 | -0.01891555 USDT |
| TRANSITION | 67 | -1.61173126 | -0.02405569 USDT |
| UPTREND | 38 | -0.99216653 | -0.02610965 USDT |

The apparently least-negative regime has only two closes and is not a promotion target.

## Entry Quality Gate

Filtered rejection counts at cutoff were:

| Reason | Count |
|---|---:|
| `REGIME_MISMATCH` | 1,537 |
| `REGIME_NOT_PERSISTENT` | 458 |
| `HYSTERESIS_BAND` | 382 |
| `SLOPE_TOO_FLAT` | 299 |
| `WARMING_UP` | 8 |
| `STALE_DATA` | 6 |

The current confidence field is explicitly heuristic and uncalibrated. Missing or abnormal data
cannot create permission to trade.

## Counterfactual Logger and No-Trade Audit

At cutoff the logger had resolved 31,846 horizon outcomes and had 80 pending. The mean modeled net
return across those outcome records was `-17.0017 bps`. Classifications were 15,704 `AVOIDED_LOSS`,
261 `MISSED_OPPORTUNITY`, and 15,881 `OBSERVED` records.

These are horizon-level outcome records across 10, 30, 60, 180, 300, and 900 seconds. They are not
31,846 distinct trades and must not be reported as such. The evidence suggests that rejecting most
raw KAMA decisions is directionally sensible because the raw family is cost-dominated, but it does
not prove that the current filtered rule set is optimal.

The current reports do not yet provide exact time spent in `NO_TRADE`, average saved loss, average
missed profit, or net filtering benefit at the distinct-candidate level. Those are outstanding audit
requirements.

## Attribution

KAMA records contain run and parameter identity, observation time, symbol, scope, direction, KAMA
values and slopes, distance, volatility proxy, regime, reference state, gross/cost/net edge,
confidence, quality reason, execution assumptions, and later outcomes. The record is immutable after
hashing. This is sufficient to answer why a KAMA candidate was accepted or rejected.

The older main-strategy ledger can reconstruct executed trades and recorded entry fields, but it does
not contain the full continuous candidate set and market path needed for a causal alternative replay.

## XAU Reference Engine

At cutoff, `XAUT_USDT` and `PAXG_USDT` produced a healthy reference fair value of `4333.70`, reference
dispersion of `3.2305 bps`, and XAU dislocation of `10.7299 bps`. The reference engine is
research-only. It cannot submit orders and it does not leak reference symbols into the wide-crypto
Entry V3 capture.

`XAU_FAIR_VALUE_V1` remains reference-only with zero trades. `XAU_COMBINED_V1` remains unimplemented.

## Strategy Health and Mandatory Final Comparison

Reported cost fields are not perfectly equivalent. The main strategy row shows separate fees while
spread and modeled slippage are embedded in its fill prices. KAMA rows show their full labeled
execution-profile costs.

| Strategy | Sample | Win rate | PF | Expectancy/trade | Net PnL | Max DD | Reported cost | MFE capture | Status |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| Current strategy | 427 | 33.02% | 0.5062 | -0.05818318 | -24.84421854 | 24.96912057 | 12.66494518 fees | Limited historical analytics | `FAILED`, not same XAU timeline |
| AMA20 raw V2 | 654 | 1.22% | 0.0108 | -0.02529230 | -16.54116247 | 16.54116247 | 16.69956517 baseline | Yes | `QUARANTINED` |
| AMA20 filtered V2 | 1 | 0.00% | 0.0000 | -0.03089797 | -0.03089797 | 0.03089797 | 0.02553457 baseline | Yes | `INSUFFICIENT_EVIDENCE` |
| XAU fair value | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A | `REFERENCE_ONLY` |
| Combined | 0 | N/A | N/A | N/A | N/A | N/A | N/A | N/A | `NOT_IMPLEMENTED` |
| Entry V3 shadow | 0 trades | N/A | N/A | N/A | N/A | N/A | N/A | Feature capture only | 15,713 rejected, execution disabled |

No row is promotion evidence.

## Quarantine

Raw KAMA is labeled `QUARANTINED` after a meaningful negative sample. The filtered family is not
quarantined because one close is below the 30-close minimum. This does not make the filtered family
healthy or eligible. It remains shadow-only and cannot reactivate or promote itself.

The main executable strategy is blocked separately by `STRATEGY_EVIDENCE_FAILED`. This preserves the
distinction between a research-family quarantine and the PAPER execution risk halt.

## Execution Realism

| Profile | Included | Raw net | Filtered net |
|---|---|---:|---:|
| IDEALIZED | Round-trip fee | -9.65159947 | -0.02036347 USDT |
| BASELINE | Fee, observed close spread, slippage buffer, funding buffer | -16.54116247 | -0.03089797 USDT |
| STRESSED | Baseline costs multiplied by 1.5 | -24.89094397 | -0.04366522 USDT |

Decision latency, submit latency, fill latency, queue position, fill probability, market impact, and
adverse selection remain unmodeled. Funding is represented in the KAMA profile buffer but the main
PAPER run reports funding as not modeled. None of these results is executable-fill proof.

## Walk-Forward and Holdout

KAMA V2 has no chronological TRAIN, VALIDATION, TEST, and FINAL HOLDOUT result. No rolling
walk-forward evaluation has run. Random splitting is prohibited. Parameters must not be tuned and
promoted from the same continuously observed sample.

The main strategy's ledger has a descriptive chronological 60/20/20 split, and all three segments
are negative. That is enough to retire the baseline. It is not enough to identify a profitable
replacement.

## Complexity Audit

### Is the complex AUTOTRADE strategy demonstrably adding value over AMA20?

**INSUFFICIENT EVIDENCE**

The main strategy and KAMA control are both negative, but they do not share one complete immutable
XAU timeline or equivalent causal execution path. Raw KAMA has a meaningful negative sample;
filtered KAMA has one close; fair-value and combined strategies do not exist; Entry V3 has no trade
outcomes; and KAMA holdout and walk-forward validation have not run.

The evidence does support two narrower conclusions:

1. The complex executable baseline has failed and should remain halted.
2. Raw KAMA is not a viable low-complexity replacement under current costs.

It does not support claiming that either complexity or simplicity is superior in a valid
same-timeline comparison.

## Dashboard Changes

The dashboard exposes:

- PAPER mode, engine state, accounting, risk, recovery, feed health, and controls.
- Persistent market scope and XAU reference health.
- Current strategy economics and the durable strategy-evidence result.
- KAMA 10/20/50, regime, raw and filtered decisions, quality reason, and evidence integrity.
- Strategy comparison, counterfactual, and execution-profile status.
- Clear `INSUFFICIENT_EVIDENCE`, `QUARANTINED`, and halt states without automatic promotion.

The browser remains a local read/control plane. Closing it does not stop the backend process, and the
dashboard cannot become an order engine.

## Implemented Improvement Status

### P0 completed

1. Independent KAMA20 raw and filtered control runs without execution influence.
2. KAMA evidence is append-only, hash-chained, restart-reconstructed, and integrity-checked.
3. Rejected candidates receive no-lookahead later-outcome records.
4. XAU reference health, quality reasons, strategy comparison, and execution profiles are visible.
5. Entry V3 reference-feed isolation is preserved.
6. The failed executable baseline now halts durably after a meaningful two-half sample.
7. Ordinary Resume cannot clear the failed strategy evidence.
8. PAPER-only, 1x leverage, 0.15 USDT XAU risk, and 15 USDT XAU maximum notional remain unchanged.

### P0 still required

1. Freeze a same-timeline immutable XAU dataset and replay the main strategy, KAMA raw, and KAMA
   filtered through equivalent cost and execution assumptions.
2. Preserve an untouched final holdout and run rolling walk-forward evaluation.
3. Produce distinct-candidate no-trade benefit metrics rather than horizon-record counts alone.
4. Establish one challenger with positive gross and net expectancy before another executable PAPER
   experiment is approved.

### P1 after P0

1. Add measured decision, submission, and fill latency to replay.
2. Model missed fills, fill ratio, queue position, funding, impact, and adverse selection.
3. Diagnose Entry V3's zero-accept state on immutable capture data without loosening production gates.
4. Calibrate confidence against out-of-sample outcomes or stop presenting it as an edge estimate.

### P3 do not do

- Do not force Resume or create a fresh run to hide the failed sample.
- Do not promote raw KAMA, filtered KAMA, XAU fair value, combined, or Entry V3.
- Do not increase leverage, capital, notional, or risk limits.
- Do not add martingale, DCA, recovery sizing, or a forced profit/trade target.
- Do not tune dozens of parameters on this single continuous sample.
- Do not spend more effort polishing the dashboard before producing causal challenger evidence.

## Tests and Repository Validation

Current validation performed for this report:

- `check.bat`: 125 unit/integration tests passed.
- Ruff: passed.
- mypy: passed for 30 source files.
- `check_ui.bat`: 5 Playwright tests passed.
- `git diff --check`: passed.
- `git fsck --no-dangling`: passed.
- `health_check.bat --once`: service healthy, trading intentionally inactive.

Coverage includes PAPER-only configuration, risk/accounting fail-closed behavior, evidence tampering,
KAMA reconstruction, quality taxonomy, reference abnormality, counterfactual storage, market-scope
persistence, Entry V3 execution rejection, strategy-evidence halt, control API origin checks, and
dashboard failure states.

The Python suite emitted `Pandas4Warning` notices for deprecated `Timestamp.utcnow` usage in runtime
test paths. They did not fail validation but should be removed before a pandas upgrade makes the
behavior unavailable.

## Runtime Verification

- Exactly one process listened on `127.0.0.1:8767` at capture.
- Mode remained `PAPER`; no live-order path was enabled.
- Engine remained `SIMULATION_READY`.
- Accounting remained `VALID` with zero open positions.
- Gate public data remained `LIVE`.
- KAMA evidence integrity remained `VALID` and observations continued to increase.
- KAMA and Entry V3 execution remained disabled.
- A same-origin Resume request reached the controller and was denied with HTTP 409.
- Three later live polling cycles remained `PAUSED`, armed=`false`, and flat.
- `HEAD` matched `origin/main` before adding this report.
- The pre-existing untracked `docs/AUDIT_REPORT_2026-09-18_POST_RESTART.md` was preserved and excluded.

## Known Limitations

- The current baseline and KAMA control are not same-timeline comparators.
- KAMA uses local REST receive time, not a reliable exchange event timestamp.
- The KAMA volatility field is a quote-step proxy, not true candle ATR.
- Filtered KAMA has only one closed shadow trade.
- Entry V3 has zero accepted candidates and zero trade outcomes.
- Fair-value and combined XAU strategies are not implemented.
- KAMA parameter sensitivity, purge/embargo, walk-forward, and final holdout are not run.
- No-trade reports lack exact duration, average saved loss, average missed profit, and net filter
  benefit at distinct-candidate level.
- The execution model omits several costs and mechanics that matter in real markets.
- Runtime run metadata predates the current checkout, so the full run is not one immutable
  code/config experiment.
- Passing tests proves current software behavior, not future profitability or live readiness.

## Remaining Risks

**P0: no validated executable edge.** Any forced Resume reallocates PAPER capital to a strategy with
negative selection, validation, holdout, and doubled-cost holdout results.

**P0: comparison validity.** Without one immutable timeline and equivalent execution assumptions,
complexity attribution remains invalid.

**P1: filtered starvation.** One pass out of 2,691 candidates may represent good rejection or an
unusable gate. The current sample cannot decide.

**P1: optimistic execution.** Latency, missed fills, adverse selection, funding, and queue effects
can worsen apparently marginal strategies.

**P1: run-version contamination.** The long-running ledger spans code evolution and cannot serve as
a clean immutable promotion experiment.

**P2: dependency drift.** pandas deprecation warnings should be corrected before upgrading the
dependency stack.

## Next Recommended Experiment

The next experiment should be one same-timeline, cost-aware XAU replay, not another live parameter
change:

1. Freeze the current KAMA evidence prefix, code revision, parameter set, symbol/reference inputs,
   and execution assumptions.
2. Define chronological selection, validation, test, and untouched final holdout windows before
   inspecting comparative PnL.
3. Replay the simplest causal variants that can be reconstructed on the same data. Do not force the
   current REST crypto strategy into an XAU comparison if its required inputs are absent.
4. Report gross edge, full modeled cost, expectancy, PF, drawdown, MFE/MAE, regime stability,
   rejection opportunity cost, and stressed execution for every family.
5. Reject any candidate with non-positive gross edge, holdout failure, unstable regimes, or
   dependence on one tiny cell.
6. Only after a challenger passes should an explicitly approved fresh PAPER run begin. Promotion
   remains manual and can never transition automatically to LIVE.

The immediate operational decision remains unchanged: keep the failed strategy halted, allow
research capture to continue, and spend the next engineering cycle on valid challenger evidence.

RESEARCH INFRASTRUCTURE INCOMPLETE
