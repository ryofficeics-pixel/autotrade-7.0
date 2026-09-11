# Autotrade 7.0 Comprehensive Variant and Current-State Report

**Evidence cutoff:** 2026-09-08 06:43:57 WIB (2026-09-07 23:43:57 UTC)  
**Workspace:** `G:\codex\autotrade 7.0`  
**Scope:** read-only inspection of runtime, checkpoints, ledgers, logs, configuration, strategy code, replay data, and verification checks  
**Trading mode:** PAPER only; no live-order path was enabled or exercised

## Verdict

The application is operational again, but the active strategy is not economically viable on the retained evidence. At the cutoff it was `ACTIVE`, accounting was `VALID`, the Gate REST feed was `LIVE`, and there was no open position. The current run nevertheless has **57 trades, 16 wins, 41 losses, 28.07% win rate, -4.22023980 USDT net PnL, and a 0.3758 profit factor**.

The dominant flaw is the entry model, not the drawdown control. The recorded edge/confidence score is inverted against outcomes: current-run winners had average recorded net edge of **40.54 bp**, while losers averaged **63.31 bp**. The old schema-v3 run shows the same failure more strongly: winners averaged **23.83 bp**, losers **101.57 bp**. Increasing the threshold in the current model would select worse trades, not better ones.

Costs matter but do not explain the loss. The current run lost **-2.57923048 USDT before fees**. Adding back the dashboard's modeled slippage estimate still leaves about **-1.92282675 USDT** of adverse price-path loss. Fees amplified a strategy that was already negative.

**Recommendation:** stop treating REST Momentum V2 as a champion. Keep the engine PAPER-only and retain all safety gates. Pause new entries until the score is replaced or recalibrated on replayable, out-of-sample data and the exit path is measured at event-level frequency. Do not reset the run or override drawdown to manufacture more samples.

## 1. Current state

| Item | State at cutoff |
|---|---|
| Service | Listening on `127.0.0.1:8767` |
| Mode | `PAPER` |
| Trading | `ACTIVE`, armed |
| Strategy | `REST Momentum Tournament` / `REST_MOMENTUM_TOURNAMENT_V2` |
| Strategy state | `WAITING_CONFIRMATION` |
| Leader at cutoff | `MARSCOIN_USDT` |
| Monitored symbols | 8 |
| Execution slots | 1 |
| Engine | `SIMULATION_READY`, NautilusTrader 1.231.0 |
| Market data | `LIVE`, Gate public REST; age 2.9 s, reported latency 953 ms |
| TradingView | `CONNECTED`, advisory only, configured weight 0 |
| Accounting | `VALID` |
| Risk | `OK` |
| Equity | 295.77976020 USDT |
| Current-run PnL | -4.22023980 USDT (-1.4067% from 300 USDT) |
| Peak-to-current drawdown | 1.4148% |
| Current-run fees | 1.64100932 USDT |
| Modeled slippage estimate | 0.65640373 USDT |
| Trades | 57 |
| Open positions | 0 |
| Run ID | `aa89e54a-e88a-4732-b254-615a58f1721a` |
| Session ID | `bcb80e4a-e7ee-4a14-8880-e803e45af782` |
| Ledger sequence | 695 |
| Checkpoint sequence | 4041 |
| Config hash | `5fb18e8f66ebf188560fc50d08f8a3f8456ac267d3254c63ad07d06bebc619ff` |
| Git commit recorded by run | `fbed164bc3ac348390aeeb645d43f92a8a429107` |

The working tree is dirty. The run metadata identifies the commit and config hash, but it does not prove that every uncommitted source file was identical throughout the run. Performance attribution to an exact working-tree code snapshot is therefore incomplete.

### Current operational chronology

- 2026-09-07 19:16:39 WIB: the current schema-v3 PAPER run was created with 300 USDT.
- 19:30:54 WIB: the first committed financial transition began.
- 19:31:12 WIB: the first trade closed.
- 2026-09-08 01:55:59 WIB: the last retained trade closed; the position was flat.
- About 02:07 WIB: DNS resolution failures made the Gate feed stale. Trading correctly changed to `PAUSED`.
- Through 06:30 WIB: the dashboard continued reporting repeated `getaddrinfo failed` feed errors.
- 06:33 WIB: the API was unreachable during the first audit probe while the watchdog remained alive.
- 06:37:54 WIB: the watchdog detected the unreachable dashboard and ran one autostart recovery.
- 06:38:23 WIB: the service returned, fresh data restored eligibility, and the watchdog restored `ACTIVE` PAPER trading.
- 06:43:57 WIB: accounting was valid, risk was OK, data was live, and no position was open.

The restart and automatic PAPER resume were watchdog actions, not audit actions. The audit did not pause, resume, flatten, reset, or place orders.

## 2. Retained run inventory

| Evidence set | Status | Trades | Wins | Losses | Win rate | Net PnL | Known fees | Notes |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Legacy pre-schema-v3 history | Archived | 53 | 9 | 44 | 16.98% | -5.31411623 | 1.24939269 | 6 trades lack attribution |
| Schema-v3 run `d3b...a910` | Archived / MAX_DRAWDOWN | 44 | 10 | 34 | 22.73% | -8.95588018 | 1.29654195 | Includes one -3.78145282 recovery flatten |
| Current run `aa89...721a` | Active at cutoff | 57 | 16 | 41 | 28.07% | -4.22023980 | 1.64100932 | Flat, accounting valid |
| **Descriptive retained total** | Mixed, not directly comparable | **154** | **35** | **119** | **22.73%** | **-18.49023621** | **4.18694396** | Different schemas/configs; do not pool for promotion |

The combined row is only a loss inventory. It is not a valid backtest because the histories use different configurations, include recovery activity, and contain six unattributed legacy trades.

### Ledger integrity

Current run:

- 695 events, sequences 1 through 695, contiguous.
- Event IDs are unique.
- 114 transition intents and 114 matching committed transitions.
- 57 signals, 57 opens, 57 closes, and 182 fills.
- Checkpoint: 57 trades, -4.22023980 USDT cumulative realized PnL, 1.64100932 USDT fees, no position.
- The application's `scan_ledger` result matches the checkpoint exactly.

Archived schema-v3 run:

- 463 events, contiguous, with 44 accounted trades.
- 43 normal `position_closed` events sum to -5.17442736 USDT.
- One separately typed `recovery_flatten` contributes -3.78145282 USDT.
- Total ledger PnL is -8.95588018 USDT and matches the archived checkpoint.

Legacy:

- 53 closed records total -5.31411623 USDT.
- 47 are attributable; six early losses totaling -0.44368044 USDT lack timestamp, symbol, side, prices, quantity, and fees.

## 3. Variant status

| Variant | Implementation | Runtime status | Performance evidence | Decision |
|---|---|---|---|---|
| `REST_MOMENTUM_TOURNAMENT_V2` | Implemented in `autotrade/paper.py`; used by dashboard | Active PAPER strategy | 154 retained trades across mixed histories; all three evidence sets lose | **Fail / pause as champion** |
| `BASELINE_REST_MOMENTUM_V2` replay adapter | Implemented inside `autotrade/entry_v3.py` | Not wired into dashboard CLI | Latest L2 replay: 276 market events, 0 candidates, 0 trades | Diagnostic only; no ranking possible |
| `MICROSTRUCTURE_ENTRY_V3` | Research implementation exists; config enabled | No dashboard integration; no `entry-v3-status.json`; order submission disabled in shadow design | Latest L2 replay completed but produced 0 entries/trades; 75 data-quality rejections | **Unvalidated; do not promote** |
| Mean Reversion | Dashboard placeholder only | `NOT IMPLEMENTED` | None | No claim possible |
| Micro Maker | Dashboard placeholder only | `NOT IMPLEMENTED` | None; queue model absent | No claim possible |
| TradingView advisory | Adapter implemented | Connected, weight 0, execution influence none | No attributable trades | Not a trading variant |

Although `[paper_strategy.entry_v3].enabled = true`, the main CLI exposes no Entry V3 shadow or replay-comparison command and the dashboard does not instantiate it. The flag therefore does not mean V3 is running.

### Entry V3 replay result

Read-only comparison was rerun against:

`data/book-replay-proof/gate-usdt-20260830T110536.351430Z-ed5b2025`

- Dataset hash: `12ab8d3c952104c6896ff65df9c63c06fd72d33db9f0f68bc55c2fb67187351d`
- Experiment: `03e293d4f6901384f19a8f4b`
- 276 normalized market events.
- Baseline: 0 candidates, 0 trades.
- Entry V3: 0 entries, 0 trades, 75 data-quality rejections.
- V3 ended with valid ETH book features in `CHOP`, but the sample is too short to evaluate expectancy.
- The previous duplicate-`connection_id` crash is no longer reproduced.

This proves deterministic code execution on this dataset, not strategy quality.

## 4. Current-run economic analysis

### Overall

| Metric | Result |
|---|---:|
| Trades | 57 |
| Wins / losses | 16 / 41 |
| Win rate | 28.07% |
| Net PnL | -4.22023980 USDT |
| Expectancy | -0.07403929 USDT/trade |
| Average win | +0.15877277 USDT |
| Average loss | -0.16489279 USDT |
| Median trade | -0.15252035 USDT |
| Profit factor | 0.3758 |
| Maximum consecutive losses | 10 |
| Closed-equity drawdown | 4.22023980 USDT |
| Average holding time | 98.72 s |

Given the observed average win and average loss, break-even requires approximately **50.96%** winners. The achieved 28.07% is not close.

### Cost bridge

| Layer | PnL | Share of final loss |
|---|---:|---:|
| Approximate price-path result before fee and configured slippage | -1.92282675 USDT | 45.6% |
| Configured slippage estimate | -0.65640373 USDT | 15.6% |
| Modeled taker fees | -1.64100932 USDT | 38.9% |
| **Final net** | **-4.22023980 USDT** | **100%** |

The slippage line is the dashboard estimate derived from fee-implied turnover and configured 2 bp per side; it is not independently measured. Spread is embedded in fill prices and cannot be isolated from these ledger records.

### By exit reason

| Exit | Trades | Win rate | Net PnL | Expectancy | Avg gross move | Finding |
|---|---:|---:|---:|---:|---:|---|
| Stop loss | 37 | 0% | -6.59657256 | -0.17828574 | -52.54 bp | Configured -35 bp stop overshot by 17.54 bp on average |
| Take profit | 15 | 100% | +2.49347093 | +0.16623140 | +68.08 bp | Insufficient frequency to offset stops |
| Time exit | 5 | 20% | -0.11713817 | -0.02342763 | +2.30 bp | Slightly positive price path becomes negative after cost |

The five-second REST polling path is too coarse for a 35 bp stop in fast-moving contracts. The median stop close was -47.11 gross bp and the worst was -90.57 bp.

### Score calibration

| Recorded net-edge bin | Trades | Win rate | Net PnL | Expectancy |
|---|---:|---:|---:|---:|
| 12–30 bp | 22 | 40.91% | -0.32692178 | -0.01486008 |
| 30–50 bp | 11 | 27.27% | -1.10192769 | -0.10017524 |
| 50–100 bp | 16 | 18.75% | -1.83377847 | -0.11461115 |
| 100+ bp | 8 | 12.50% | -0.95761186 | -0.11970148 |

The score is monotonically worse as it rises in this sample. The code defines `expected_gross_bps` as the absolute recent price move, subtracts fixed estimated costs, and derives confidence directly from that value. It estimates continuation from the move itself; it does not estimate the probability that the move continues. Large moves are therefore labeled high-edge even when they are exhausted.

### By symbol

| Symbol | Trades | Win rate | Net PnL | Expectancy | Profit factor |
|---|---:|---:|---:|---:|---:|
| PUMP_USDT | 3 | 33.33% | -0.01253792 | -0.00417931 | 0.9385 |
| AKE_USDT | 7 | 28.57% | -0.66436330 | -0.09490904 | 0.2554 |
| ARB_USDT | 9 | 11.11% | -0.80602819 | -0.08955869 | 0.1541 |
| BULLA_USDT | 8 | 25.00% | -0.81887225 | -0.10235903 | 0.2537 |
| PONS_USDT | 23 | 39.13% | -0.94529124 | -0.04109962 | 0.6218 |
| 牛来_USDT | 7 | 14.29% | -0.97314690 | -0.13902099 | 0.1274 |

No current-run symbol is net profitable. PUMP is closest to flat but has only three trades. PONS accounts for 40% of current-run trades and 22% of the loss; concentration increased exposure to one unstable contract without proving an edge.

### By side

| Side | Trades | Win rate | Net PnL | Expectancy | Profit factor |
|---|---:|---:|---:|---:|---:|
| Long | 31 | 22.58% | -2.55726191 | -0.08249232 | 0.2952 |
| Short | 26 | 34.62% | -1.66297789 | -0.06396069 | 0.4690 |

Both directions lose. Disabling longs alone would reduce loss in this sample but would not make the strategy viable.

### By WIB closing hour

| Hour | Trades | Win rate | Net PnL | Expectancy |
|---|---:|---:|---:|---:|
| 19:00 | 3 | 33.33% | -0.22393948 | -0.07464649 |
| 20:00 | 8 | 25.00% | -0.80921825 | -0.10115228 |
| 21:00 | 9 | 33.33% | -0.51825119 | -0.05758347 |
| 22:00 | 8 | 50.00% | -0.19152399 | -0.02394050 |
| 23:00 | 16 | 18.75% | -1.52174970 | -0.09510936 |
| 00:00 | 6 | 16.67% | -0.55617244 | -0.09269541 |
| 01:00 | 7 | 28.57% | -0.39938475 | -0.05705496 |

No observed hour is profitable after costs. Time filtering is not justified from this sample.

## 5. Flaw analysis

### Demonstrated flaws

1. **The entry score is economically miscalibrated.** Higher recorded edge/confidence predicts worse outcomes in two schema-v3 runs. This is the primary failure.
2. **The strategy loses before fees.** The current run remains about -1.92 USDT after adding back fees and configured slippage. Cheaper execution alone cannot repair it.
3. **Stops are sampled too slowly for the target.** A 35 bp configured stop closes at -52.54 gross bp on average. REST polling and volatile symbols create overshoot.
4. **The selection heuristic rewards extreme 24-hour movement.** `rank_tickers` caps absolute daily change at 10% and gives it up to 40 score points. The source itself marks this heuristic for replacement with validated microstructure scores. This selection is compatible with momentum chasing and adverse selection.
5. **Confidence is not probabilistic.** It is `expected_net_bps / entry_threshold_bps`, capped at one. In the current sample, losers have slightly higher average confidence than winners.
6. **The research challenger is not operationally connected.** Entry V3 configuration is present, but the normal CLI/dashboard path never starts it and no live-shadow artifact exists.
7. **Execution realism is incomplete.** Decision, submission and fill latency, fill ratio, adverse selection, funding, and queue position are explicitly `NOT_MODELED` in runtime state.
8. **Operational recovery has a blind interval.** The dashboard was unreachable around 06:33 and the 15-minute watchdog recovered it at 06:37. The safety behavior was correct, but outage detection is slow for an always-on process.
9. **The verification gate is not clean.** Unit tests pass, but lint and type checks fail on test code.

### Plausible but not yet proven

- Large REST-observed moves may be stale or exhausted by the time the strategy enters.
- Dynamic top-mover universe churn may cause repeated entry into temporary pumps/dumps.
- Five-second snapshots may miss intra-interval MFE/MAE and misstate attainable stops and take profits.
- Fixed taker execution may be unnecessarily expensive, but a maker model cannot be trusted without queue/depth replay.

### Unknown because evidence is absent

- Out-of-sample V3 expectancy.
- Live-shadow V2-versus-V3 opportunity agreement.
- Measured end-to-end decision, submit, acknowledgement, and fill latency.
- Funding and liquidation effects.
- Real fill ratio and rejection rate.
- Whether the strategy survives multiple market regimes.

## 6. Fix plan

### P0 — Do now

1. **Pause REST Momentum V2 new entries; do not reset the run.**
   - Component: dashboard control / current PAPER engine.
   - Benefit: stops spending simulated risk budget on a demonstrably negative model while preserving evidence.
   - Downside: no new strategy trades until a challenger passes.
   - Verification: runtime remains healthy and collecting market data with `trading_state=PAUSED`, no open position, and ledger sequence unchanged except explicitly non-financial events.

2. **Replace the edge/confidence definition before tuning thresholds.**
   - Components: `autotrade/paper.py` entry logic and research evaluator.
   - Change: treat absolute recent move as a feature, not expected profit. Estimate continuation probability and conditional return from replay/forward samples. Reject a model whose score buckets are not monotonically improving out of sample.
   - Benefit: attacks the proven inversion.
   - Downside: requires more clean data; may produce no trades.
   - Rejection test: on chronological out-of-sample data, upper score bins must have better net expectancy than lower bins and the lower confidence bound of promoted trades must exceed zero after all modeled costs.

3. **Add a deterministic performance gate to the strategy lifecycle.**
   - Component: research/promotion logic, not the safety gate.
   - Change: no variant can become active solely because configuration says `enabled=true`. Require a recorded experiment, immutable data/code/config hashes, enough trades, positive net expectancy, acceptable drawdown, and score calibration.
   - Benefit: prevents unvalidated code from silently becoming a champion.
   - Verification: current REST V2 fails; V3 remains shadow because it has zero trades.

### P1 — Required for credible PAPER results

1. **Drive exits from the public WebSocket event stream or a proven synchronized local book.**
   - Keep REST as fallback/monitoring, not the primary stop clock.
   - Measure stop trigger-to-fill overshoot. Target must be derived from captured latency and book depth, not guessed.
   - Verification: replay and forward PAPER distributions show materially lower stop overshoot without optimistic fills.

2. **Run Entry V3 as an actual no-order live shadow.**
   - Expose a safe CLI path, persist `entry-v3-status.json`, and keep `order_submission_enabled=false`.
   - Feed V2 and V3 the same timestamped events and compare opportunities without trading.
   - Verification: deterministic replay hash plus continuous shadow records with no order events.

3. **Capture enough valid L2/trade data.**
   - The existing 276-event dataset is a plumbing proof, not a performance sample.
   - Collect multiple regimes and preserve sequence-gap/resnapshot evidence.
   - Verification: nonzero candidates/trades for both variants and separate development/validation/out-of-sample partitions.

4. **Constrain universe selection using validated forward features.**
   - Remove direct promotion based on absolute 24-hour change.
   - Add listing age, depth, turnover stability, spread stability, pump/exhaustion measures, and symbol-level kill evidence where data supports them.
   - Do not create permanent blacklists from three to nine trades.

5. **Model the missing execution costs.**
   - Add measured latency, partial/unfilled behavior, adverse selection, funding, and mark-price accounting.
   - Maker execution remains research-only until queue-ahead behavior is conservative and validated.

### P2 — Reliability and diagnostics

1. Reduce dashboard outage detection time or add process supervision with bounded backoff and a single-instance lock.
2. Persist exact working-tree code hash in every active run, not only the Git commit.
3. Record per-trade MFE, MAE, trigger time, trigger price, and stop overshoot for REST V2 as well as V3.
4. Add score-calibration, symbol concentration, and cost-bridge panels to the dashboard.
5. Fix the two Ruff B023 errors and the mypy return annotation mismatch; replace deprecated `Timestamp.utcnow` calls.

### P3 — Ignore for now

- Mean Reversion implementation.
- Micro Maker implementation.
- Live trading.
- Higher leverage.
- Drawdown bypasses.
- Cosmetic dashboard work unrelated to evidence quality.

These are lower value than fixing the entry signal and replay/shadow evidence path.

## 7. Verification results

| Check | Result |
|---|---|
| Unit tests | **82 passed** |
| Ruff | **Failed:** two B023 loop-variable binding findings in `tests/test_integrity.py:233-234` |
| mypy | **Failed:** one incompatible return annotation at `tests/test_integrity.py:119` |
| Playwright | **5 passed** |
| Entry V3 replay | Completed successfully; zero trades, so no performance conclusion |
| Current API | Healthy at cutoff |
| Current accounting reconciliation | Valid |

The passing tests show that specified software behavior works. They do not prove profitability, correct market selection, realistic execution, or live readiness.

## 8. Council warning

Continuing to reset the account after drawdown would convert a useful falsification signal into repeated simulated losses. The retained evidence already says the current REST Momentum score is selecting the wrong opportunities. More trades from the same unchanged rule add quantity, not quality.

## 9. Exact next action

Use the existing Pause control while flat, keep market-data capture running, then implement and verify the P0 score-calibration gate before allowing another REST Momentum entry. Entry V3 should remain no-order shadow research until it generates a statistically meaningful out-of-sample comparison.

