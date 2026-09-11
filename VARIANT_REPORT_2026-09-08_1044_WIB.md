# Autotrade 7.0 Comprehensive Variant Report — Requested 10:44 WIB Cutoff

**Requested cutoff:** 2026-09-08 10:44 WIB (2026-09-08 03:44 UTC)  
**Actual collection:** 2026-09-10 07:45–07:52 WIB (2026-09-10 00:45–00:52 UTC)  
**Baseline:** `VARIANT_REPORT_2026-09-08_CURRENT_STATE.md`  
**Workspace:** `G:\codex\autotrade 7.0`  
**Scope:** runtime, checkpoint, ledger, logs, configuration, repository, replay, and software checks  
**Control boundary:** read-only audit; no pause, resume, flatten, new run, risk override, reconfiguration, or order action was performed

## Verdict

The active REST Momentum V2 strategy remains economically failed. At the requested cutoff it had **84 completed trades, 27 wins, 57 losses, 32.14% win rate, and -4.81669985 USDT realized PnL**. By the latest committed ledger state it had **105 completed trades, 35 wins, 70 losses, 33.33% win rate, and -6.67956621 USDT realized PnL**. Losses therefore continued after the requested cutoff.

The current service is available and the public Gate feed is live, but trading is correctly **HALTED / RECOVERY_REQUIRED** because a simulated `ZEC_USDT` long position persisted across a restart. Accounting is `VALID`; this is not a current drawdown breach. Resume is disabled and flatten is the only enabled recovery action. There is no safe countdown or automatic override for this state.

The demonstrated problem is still signal quality plus coarse execution. Current-run completed trades lost approximately **-3.60430773 USDT before fees**, and approximately **-2.37420433 USDT before both fees and configured slippage**. Costs amplified the loss but did not create it. Winners carried an average recorded net edge of **44.15 bp**; losers carried **63.42 bp**. The score remains inverted. Stops averaged **53.37 bp adverse movement** against a configured 35 bp target, an average overshoot of **18.37 bp**.

**Decision:** do not resume this strategy unchanged. First close the persisted PAPER position through the documented recovery control, keep new entries paused, then repair and validate the entry score and event-speed exit path. Do not weaken recovery, accounting, stale-data, persistence, or drawdown controls.

## 1. Requested-cutoff reconstruction

The scheduler did not execute at 2026-09-08 10:44 WIB. The requested-cutoff values below are reconstructed from immutable schema-v3 ledger events through 03:44 UTC.

| Item | Reconstructed state at requested cutoff |
|---|---:|
| Run ID | `aa89e54a-e88a-4732-b254-615a58f1721a` |
| Ledger events | 1,016, sequences 1–1,016 |
| Completed trades | 84 |
| Wins / losses | 27 / 57 |
| Win rate | 32.14% |
| Realized PnL | -4.81669985 USDT |
| Modeled fees | 2.44845672 USDT |
| Modeled slippage estimate | 0.97938269 USDT |
| Approx. result before fees | -2.36824313 USDT |
| Approx. result before fees and configured slippage | -1.38886044 USDT |
| Open positions | 0 |
| Last completed trade | 2026-09-08 10:28:38 WIB, `PONS_USDT` short, take profit |

The cutoff reconstruction proves the strategy was already negative before the later recovery event and later trades.

## 2. Actual current state

| Item | State at actual collection |
|---|---|
| Service | Available on `127.0.0.1:8767` |
| Mode | `PAPER`; live trading disabled |
| Trading | `HALTED`; not armed |
| Strategy | `REST_MOMENTUM_TOURNAMENT_V2` |
| Strategy/risk status | `RECOVERY_REQUIRED` |
| Engine | `SIMULATION_READY`; NautilusTrader 1.231.0 |
| Market data | `LIVE`; Gate public REST; observed age 2.3 s |
| Accounting | `VALID` |
| Persisted equity | 293.30519543 USDT |
| Cumulative realized PnL | -6.67956621 USDT |
| Peak-to-current persisted drawdown | 2.2396% |
| Daily PnL for current risk day | -0.01523836 USDT |
| Completed trades | 105 |
| Open positions | 1 simulated position |
| Position | `ZEC_USDT` LONG, 0.025123 at 1213.1 |
| Current observed price | 1229.55 at the final API probe |
| Position opened | 2026-09-09 11:32:35 WIB |
| Resume allowed | No |
| Flatten allowed | Yes |
| Run ID | `aa89e54a-e88a-4732-b254-615a58f1721a` |
| Runtime session | `0c1d59e7-f707-4f44-a6f2-d0abd3cb32cf` |
| Last financial-event session | `191f4eef-9d53-4c64-8e32-655be8d0e449` |
| Ledger / checkpoint | event 1,271 / checkpoint 7,013 |

The different checkpoint and event sequence numbers are expected: checkpoint sequence counts persistence writes, while ledger sequence counts immutable events. The application integrity scanner reconciled the checkpoint against ledger event 1,271 exactly.

The displayed equity excludes current mark-to-market profit or loss and already deducts the open-position entry fee. The observed price was favorable to the long at collection, but that is not a realized result and does not make the position safe. It has been unmanaged since the restart and must be recovered explicitly.

### Operational chronology after the requested cutoff

- 2026-09-08 14:25:03 WIB: a persisted `PONS_USDT` short was recovery-flattened for -0.84181366 USDT.
- 2026-09-08 16:23:54 WIB: the last normal trade in the ledger closed.
- 2026-09-09 11:04 WIB: the watchdog restored eligible PAPER operation after a restart.
- 2026-09-09 11:32:35 WIB: the current `ZEC_USDT` long opened and committed at ledger sequence 1,271.
- 2026-09-09 12:29 WIB: the watchdog reported `HALTED / RECOVERY_REQUIRED` with the persisted position.
- 2026-09-10 07:44 WIB: the watchdog again confirmed the service was healthy but trading remained halted; it did not bypass recovery.

## 3. Ledger, checkpoint, and attribution

### Current run

- 1,271 schema-v3 events; sequences 1–1,271 are contiguous.
- 210 transition intents and 210 matching commits.
- 106 signals, 106 submitted entry orders, 106 opens, 104 normal closes, one recovery flatten, and one currently open position.
- 324 fill events.
- Five financial-event sessions are retained.
- Application `scan_ledger` result: 105 trades, -6.679566214363925450 USDT realized PnL, 3.090496848013025450 USDT total modeled fees, and exactly one attributed open position.
- `reconcile_checkpoint` completed successfully with no mismatch.

The total fee figure includes the current open position's 0.01523836 USDT entry fee. Completed-trade fees are therefore approximately 3.07525849 USDT.

### Retained evidence sets

| Evidence set | Status | Trades | Wins | Losses | Win rate | Net PnL | Known fees | Attribution |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Legacy pre-v3 history | Archived | 53 | 9 | 44 | 16.98% | -5.31411623 | 1.24939269 | 6 trades unattributed |
| Schema-v3 `d3b...a910` | Archived / failed | 44 | 10 | 34 | 22.73% | -8.95588018 | 1.29654195 | Includes -3.78145282 recovery flatten |
| Schema-v3 `aa89...721a` | Halted / recovery required | 105 | 35 | 70 | 33.33% | -6.67956621 | 3.09049685 | One attributed open PAPER position |
| **Descriptive total** | Mixed; not comparable for promotion | **202** | **54** | **148** | **26.73%** | **-20.94956263** | **5.63643148** | Different schemas/configurations |

The combined row is only a loss inventory. It is not a valid backtest or pooled performance estimate.

## 4. Variant inventory

| Variant | Implementation and data path | Runtime status | Sample / result | Blocker and decision |
|---|---|---|---|---|
| `REST_MOMENTUM_TOURNAMENT_V2` | `autotrade/paper.py`; Gate public REST snapshots; `NAUTILUS_PAPER_REST_V2` | **HALTED / RECOVERY_REQUIRED**; one open simulated position | Current run: 105 completed, 35W/70L, -6.67956621 net; 3.09049685 fees including open fee; 1.23619874 modeled slippage including open entry | Negative before costs, inverted score, coarse stop timing. Fail as champion. |
| `BASELINE_REST_MOMENTUM_V2` replay adapter | `autotrade/entry_v3.py`; normalized L2 replay only | Research-only; not dashboard execution | 276 market events, 0 candidates, 0 entries, 0 trades | Dataset cannot rank it. Diagnostic only. |
| `MICROSTRUCTURE_ENTRY_V3` | `autotrade/entry_v3.py`; L2/book/trade features; conservative taker replay | Config flag enabled, but no dashboard integration and no `logs/entry-v3-status.json`; order submission hard-disabled in shadow design | 276 events, 0 entries/trades; 75 data-quality rejections; final ETH state `CHOP` | Plumbing works, profitability untested. Do not promote. |
| TradingView advisory | Local advisory adapter | Connected at collection; weight 0; execution influence none | No attributable trades or PnL | Not a strategy variant. |
| Legacy REST histories | Archived JSONL/checkpoints | Disabled/archived | 97 trades across legacy and first v3 run, both losing | Preserve separately; not restartable evidence. |
| Mean Reversion | Dashboard placeholder / research concept | `NOT IMPLEMENTED` | No data, replay, trades, fees, slippage, or drawdown | Do not build until the evidence pipeline works. |
| Micro Maker | Dashboard placeholder / research concept | `NOT IMPLEMENTED` | No queue-aware simulator or results | Do not build until queue and fill modeling exist. |

No active shadow challenger exists despite Entry V3 being enabled in configuration. An enabled flag without process wiring, status artifact, and event flow is not an operational variant.

## 5. Current-run economic analysis

### Overall, including recovery activity

| Metric | Result |
|---|---:|
| Completed trades | 105 |
| Wins / losses | 35 / 70 |
| Win rate | 33.33% |
| Realized PnL | -6.67956621 USDT |
| Expectancy | -0.06361492 USDT/trade |
| Normal-close net profit factor | 0.4951 |
| Completed-trade modeled fees | 3.07525849 USDT |
| Completed-trade modeled slippage estimate | 1.23010340 USDT |
| Approx. result before fees | -3.60430773 USDT |
| Approx. result before fees and configured slippage | -2.37420433 USDT |
| Normal stop exits | 61 |
| Average stop adverse move | 53.37 bp |
| Average overshoot above 35 bp target | 18.37 bp |

The recovery flatten is reported separately from normal strategy closes. It is a real accounting loss but not a normal exit sample.

### Exit reason

| Exit | Trades | Wins | Net PnL | Fees | Gross before fees | Finding |
|---|---:|---:|---:|---:|---:|---|
| Stop loss | 61 | 0 | -11.21343784 | 1.77537918 | -9.43805866 | Dominant loss source; slow exit sampling overshoots target |
| Take profit | 31 | 31 | +5.48671969 | 0.91396034 | +6.40068003 | Too few wins to offset stops |
| Time exit | 12 | 4 | -0.11103440 | 0.35662793 | +0.24559353 | Positive pre-fee path becomes negative after fees |
| Recovery flatten | 1 | 0 | -0.84181366 | 0.02929104 | -0.81252263 | Operational recovery loss; keep separate |

### Score calibration

| Recorded net-edge bin | Trades | Wins | Net PnL | Average net/trade |
|---|---:|---:|---:|---:|
| Under 20 bp | 21 | 8 | -0.82885848 | -0.03946945 |
| 20–40 bp | 36 | 13 | -1.92259668 | -0.05340546 |
| 40–60 bp | 14 | 3 | -0.73094906 | -0.05221065 |
| 60+ bp | 33 | 11 | -2.35534833 | -0.07137419 |

These 104 normal closes show no profitable score bucket. Average recorded edge is 44.15 bp for winners versus 63.42 bp for losers; average confidence is 0.9399 for winners versus 0.9529 for losers. Raising the existing threshold would select worse evidence, not better evidence.

### Loss concentration

| Slice | Trades | Wins | Net PnL | Interpretation |
|---|---:|---:|---:|---|
| Long | 51 | 14 | -3.68290201 | Worse direction, but shorts also lose |
| Short, normal closes | 53 | 21 | -2.15485054 | Negative despite higher win rate |
| `UAI_USDT` | 11 | 1 | -1.60507370 | Largest normal-close symbol loss |
| `PONS_USDT`, normal closes | 32 | 14 | -1.20374999 | Highest concentration; recovery adds another -0.84181366 |
| `牛来_USDT` | 7 | 1 | -0.97314690 | Thin sample but strongly negative |
| `ARB_USDT` | 14 | 3 | -0.82295833 | Negative before fees |
| `BULLA_USDT` | 8 | 2 | -0.81887225 | Negative before fees |
| `MARSCOIN_USDT` | 10 | 5 | +0.39188035 | Only meaningfully positive normal-close symbol |
| `WLD_USDT` | 4 | 3 | +0.17328634 | Too small for promotion |

By closing hour, 23:00 WIB was the largest loss cluster: 16 trades, 3 wins, -1.52174970 USDT. Hour 10:00 WIB was positive (+0.47560370 across seven trades), but the sample is too small and selected after inspection; it is not a validated time filter.

By recorded regime-move magnitude, signals at 100 bp or greater produced 51 trades and -4.64045450 USDT, including -3.15708443 before fees. This supports the demonstrated momentum-chasing/exhaustion diagnosis. The 30–60 and 60–100 bp groups were slightly positive before fees but negative after fees. Regime labels are derived from the strategy's own move feature, not an independently validated regime classifier.

## 6. Demonstrated flaws, hypotheses, and unknowns

### Demonstrated

1. **The score is inverted.** Higher declared edge and confidence correspond to worse outcomes in both schema-v3 runs.
2. **The strategy has negative gross edge.** Adding back fees and configured slippage does not make either the requested-cutoff or current sample profitable.
3. **The stop mechanism is too coarse for a 35 bp target.** Five-second REST polling and volatile contracts produce 18.37 bp average stop overshoot.
4. **Large moves are rewarded as edge.** The 100+ bp regime-move group is the worst group before and after fees.
5. **Symbol concentration magnifies model error.** `PONS_USDT` received 32 normal closes plus a recovery flatten; `UAI_USDT` won only once in 11 trades.
6. **Restart recovery leaves a position unmanaged.** Safety correctly blocks resume, but the current PAPER position has remained open since September 9 and requires explicit recovery.
7. **Execution realism remains incomplete.** Decision, submit and fill latency, adverse selection, funding, fill ratio, and queue position are not modeled.
8. **Exact source attribution is incomplete.** The run records commit `fbed164`, but the working tree is dirty: 20 tracked files differ with 1,794 insertions and 202 deletions, plus untracked Entry V3 and reports. The current code hash is `ef89661b63de18e8edbb04bc462e674b8cfd5ae8bee4135923a392e13845feca`.
9. **The verification gate is not clean.** Unit and browser tests pass, but Ruff and mypy fail.

### Plausible but not proven

- REST-observed bursts may be exhausted before entry.
- Top-mover universe selection may repeatedly select pumps, dumps, or unstable new contracts.
- Snapshot sampling may miss intra-interval MFE/MAE and create unrealistically late exits.
- A conservative maker path might reduce costs, but there is no queue model to support that claim.

### Unknown

- V3 out-of-sample expectancy; the only replay has zero trades.
- Live-shadow V2/V3 agreement and disagreement.
- True end-to-end decision-to-fill latency.
- Funding, liquidation, rejection, and partial-fill effects under real exchange conditions.
- Robustness across chronological regimes.

## 7. Replay and configuration snapshot

Current config remains PAPER-only with live trading disabled, 1x leverage, 30 USDT notional, 5-second polling, 15-second stale threshold, 35 bp stop, 55 bp take profit, 300-second maximum hold, 12 bp minimum net edge, 0.60 minimum confidence, 6 USDT daily loss, and 3% maximum drawdown.

The current config SHA-256 exactly matches run metadata: `5fb18e8f66ebf188560fc50d08f8a3f8456ac267d3254c63ad07d06bebc619ff`.

Entry V3 replay was rerun read-only on `data/book-replay-proof/gate-usdt-20260830T110536.351430Z-ed5b2025`:

- Dataset hash: `12ab8d3c952104c6896ff65df9c63c06fd72d33db9f0f68bc55c2fb67187351d`
- Experiment: `03e293d4f6901384f19a8f4b`
- Deterministic event hash: `5bd41eb4838fc4b320e662ff667f88288a36b0e9729bd647a14c11417763bbe6`
- 276 normalized events.
- Baseline: zero candidates, entries, trades, PnL, fees, slippage, or drawdown.
- V3: zero candidates, entries, trades, PnL, fees, slippage, or drawdown; 75 data-quality rejections.
- Final valid ETH feature state: `CHOP`.

This proves deterministic execution and fail-closed data handling on one short dataset. It provides no profitability or live-readiness evidence.

## 8. Smallest safe fix plan

### P0 — Do now

1. **Recover the persisted PAPER position, then keep entries paused.**
   - Component: existing dashboard `FLATTEN PAPER POSITIONS`, then pause state.
   - Expected benefit: closes an unmanaged simulation and restores a flat, reconcilable baseline.
   - Downside: realizes the simulated mark at recovery time.
   - Acceptance: one attributed recovery event commits; checkpoint and ledger reconcile; open positions become zero; no live order path exists.

2. **Replace the edge/confidence calculation before any threshold tuning.**
   - Component: V2 feature/decision logic and research evaluator.
   - Expected benefit: removes the proven assumption that move magnitude equals continuation edge.
   - Downside: likely fewer or no trades until evidence improves.
   - Acceptance: on chronological holdout data, score buckets improve monotonically and promoted trades have positive net expectancy after fees, spread, slippage, and measured latency. Reject otherwise.

3. **Add a deterministic promotion gate.**
   - Component: strategy lifecycle and experiment artifacts.
   - Expected benefit: prevents `enabled=true` from being mistaken for validated or active.
   - Acceptance: current V2 fails; V3 stays shadow with zero trades; promotion requires immutable data/code/config hashes, sufficient samples, positive holdout expectancy, and acceptable drawdown.

### P1 — Required for credible PAPER evidence

1. Drive stop observation from synchronized public WebSocket/L2 events, retaining REST as fallback. Accept only if forward PAPER stop overshoot materially decreases without optimistic fills.
2. Wire V3 into a no-order live shadow with a persisted status artifact and shared timestamped inputs. Confirm zero order events by test and ledger inspection.
3. Capture multiple clean datasets and use chronological development, validation, and untouched holdout partitions.
4. Replace absolute 24-hour-move ranking with forward-validated liquidity, spread stability, depth, listing-age, and exhaustion features.
5. Add measured latency, partial/unfilled behavior, adverse selection, funding, and mark-price accounting to evaluation.

### P2 — Reliability and diagnostics

1. Persist a working-tree code hash with every run and session.
2. Record per-trade MFE, MAE, trigger time, trigger price, and stop overshoot.
3. Shorten service outage detection with bounded supervision and a single-instance lock.
4. Fix the two Ruff B023 findings and the mypy test annotation mismatch; replace deprecated `Timestamp.utcnow` calls.
5. Add score calibration, concentration, and cost bridge to the dashboard without creating any override control.

### P3 — Ignore for now

- Mean Reversion and Micro Maker implementation.
- Live trading, leverage increases, martingale/DCA, or drawdown/recovery overrides.
- Cosmetic dashboard work unrelated to evidence quality.
- Fine-tuning thresholds on this same observed sample.

## 9. Verification snapshot

| Check | Result |
|---|---|
| Unit/integration tests | **82 passed** in 11.891 s |
| Ruff | **Failed:** two B023 loop-variable binding findings at `tests/test_integrity.py:233-234` |
| mypy | **Failed:** incompatible test helper return type at `tests/test_integrity.py:119` |
| Playwright | **5 passed** |
| Ledger scan and checkpoint reconciliation | **Passed**; exact current totals and open position match |
| Entry V3 replay | **Completed deterministically**, but zero trades |
| Live-order path | Configuration remains PAPER-only; no live order action was introduced or exercised |
| Trading mutation during audit | None |

The software checks show that tested behavior mostly works. They do not prove profitability, correct calibration, execution realism, or readiness for live capital. The failed lint/type gate also means the repository does not currently satisfy its own definition of done.

## 10. Council warning and next action

The highest-risk mistake is to interpret `accounting=VALID` or a favorable current mark as permission to resume. The position is validly recorded but operationally unrecovered. Bypassing recovery would stack new exposure on an unmanaged state, and resuming V2 unchanged would continue a strategy that loses before costs.

**Next action:** use the existing recovery flatten control for the PAPER position, verify the resulting commit and flat checkpoint, then keep new entries paused while P0 score calibration and the no-order shadow evidence path are implemented. Do not start a fresh run merely to erase the loss history.
