# AUTOTRADE 7.0 Loss Root-Cause Audit and Remediation

## Verdict

The active PAPER strategy should not trade again in its current form. It has no demonstrated net
edge. At the fixed cutoff, 426 normal trades lost 24.57914898 USDT, the profit factor was 0.5089,
and all chronological selection, validation, and holdout segments were negative. The loss is not a
dashboard, accounting, or restart illusion. The strategy's price outcomes are negative before fees,
and fees approximately double the normal-strategy loss.

The correct immediate improvement is to stop allocating PAPER capital to this strategy. This change
implements a durable strategy-evidence halt. It does not claim to have discovered profitable alpha.

## Scope and Fixed Cutoff

- Application: AUTOTRADE 7.0, Phase 1 PAPER simulator only.
- Market scope at cutoff: `WIDE_CRYPTO`.
- Cutoff: 2026-09-23 03:03:12 UTC, 2026-09-23 10:03:12 WIB.
- Run: `aa89e54a-e88a-4732-b254-615a58f1721a`.
- Session: `f27d2238-5139-442a-870d-90ab07bedf6b`.
- Source checkout at evidence capture: `223191d6602e66c390127136f694886cac8047b1`.
- Runtime-reported run commit: `fbed164bc3ac348390aeeb645d43f92a8a429107`.
- Runtime config hash: `5fb18e8f66ebf188560fc50d08f8a3f8456ac267d3254c63ad07d06bebc619ff`.
- Event ledger: `data/runs/aa89e54a-e88a-4732-b254-615a58f1721a/events.jsonl`.
- Ledger at cutoff: 5,322 lines, 5,868,386 bytes.
- Ledger SHA-256: `9ED97E351DD301B8516609BCC1730BE5BE19AE4A78AC2D7B28ED565D8C978F90`.
- Checkpoint sequence at capture: 30,429; last event sequence: 5,322.

At the cutoff, the runtime was deliberately paused and flat, market data was live, accounting was
valid, and equity was 280.17850336 USDT. Healthy operation proves that the software is running and
reconciled. It does not prove that the strategy is profitable.

## Evidence Register

| Evidence | Use | Boundary |
|---|---|---|
| Current run schema-v3 ledger | Reconstruct closes, fees, classifications, and chronology | PAPER fills, not exchange fills |
| `paper-state.json` and `/api/state` | Confirm run identity, accounting, equity, state, freshness, and scope | Point-in-time operational state |
| `autotrade.profitability_cli` | Chronological 60/20/20 split, doubled-cost holdout, deterministic bootstrap intervals | One observed PAPER run, not causal replay |
| Dashboard profitability diagnostics | Direction, exit, score, holding-time, and cost summaries | Descriptive, not a promotion test |
| Current source, configuration, tests, and docs | Trace implementation and safety controls | Runtime began on an older reported commit |

Manual and outage-held recovery outcomes are excluded from normal-strategy expectancy. This prevents
restart price movement from being misreported as strategy alpha.

## What the Account Actually Did

| Measure | Normal strategy | Entire run |
|---|---:|---:|
| Closed trades | 426 | 435 |
| Wins / losses | 141 / 285 | Includes 5 recovery and 4 manual closes |
| Win rate | 33.10% | Not used for strategy conclusion |
| Gross price PnL | -11.94432131 USDT | Mixed classifications |
| Fees | 12.63482770 USDT | 12.90806603 USDT |
| Net PnL | -24.57914898 USDT | -19.82149665 USDT |
| Expectancy | -0.05769753 USDT/trade | Mixed classifications |
| Profit factor | 0.5089 | Mixed classifications |
| Average winner | 0.18062719 USDT | Mixed classifications |
| Average loser | -0.17560555 USDT | Mixed classifications |
| Break-even win rate | 49.30% | Mixed classifications |
| Maximum normal drawdown | 24.96912057 USDT | Not a live-risk forecast |

The entire-run result looks less negative only because five outage-held recovery closes contributed
about 4.94448 USDT. That result came from exposure held across an interruption and is not repeatable
evidence for the entry strategy. Four manual closes lost about 0.18683 USDT. The normal strategy is
the relevant decision set.

## Why the Strategy Keeps Losing

### 1. The entry signal has negative realized edge

Normal gross price PnL was already -11.9443 USDT before deducting 12.6348 USDT of fees. The strategy
therefore does not merely lose because Gate fees are high. Its selected moves, including modeled
slippage embedded in fill prices, lose money before the separate fee deduction.

Both directions lose: long trades netted about -12.802 USDT and short trades about -11.777 USDT.
This is not a one-sided market-direction mistake.

### 2. The realized payoff cannot support the observed win rate

The average win was 0.1806 USDT and the average loss was 0.1756 USDT, nearly one-for-one. That payoff
requires a 49.30% break-even win rate. The observed win rate was only 33.10%, a gap of 16.20 percentage
points. Nominal 55 bp take-profit and 35 bp stop settings do not survive actual early exits, fills,
and signal behavior as a 55:35 realized payoff.

Exit outcomes make the imbalance explicit:

| Exit reason | Trades | Net PnL |
|---|---:|---:|
| `STOP_LOSS` | 243 | -47.62895 USDT |
| `TAKE_PROFIT` | 125 | +24.74025 USDT |
| `TIME_EXIT` | 58 | -1.69045 USDT |

Take-profit gains recover only about half of stop-loss damage. Time exits add another loss.

### 3. High turnover monetizes the weak signal into fees

The strategy completed about 13.28 normal trades per active hour across 32.08 active hours. Normal
turnover was 25,269.65 USDT. Fees of 12.6348 USDT then increased the loss from -11.9443 USDT in price
outcomes to -24.5791 USDT net. Reducing fees would help, but it would not make the current signal
positive because pre-fee price PnL is negative.

### 4. There is no stable profitable segment to enable safely

Every observed holding bucket lost money: under one minute -14.4386 USDT, one to five minutes
-8.2840 USDT, and over five minutes -1.8565 USDT. The recorded confidence-score buckets also lost.
The confidence value is therefore a heuristic, not a calibrated probability of profit.

The first and second chronological halves were both negative:

| Window | Trades | Net PnL | Profit factor | Expectancy |
|---|---:|---:|---:|---:|
| First half | 213 | -11.40415 USDT | 0.5329 | -0.05354 USDT |
| Second half | 213 | -13.17499 USDT | 0.4861 | -0.06185 USDT |

Performance deteriorated rather than recovering in the later half.

### 5. Chronological validation rejects a luck explanation

The deterministic chronological split produced:

| Segment | Trades | Net PnL | Profit factor | Expectancy | Bootstrap 95% expectancy interval |
|---|---:|---:|---:|---:|---:|
| Selection | 255 | -15.4954 | 0.4908 | -0.06077 | [-0.08383, -0.03735] |
| Validation | 85 | -3.8650 | 0.5689 | -0.04547 | [-0.08393, -0.00277] |
| Holdout | 86 | -5.2187 | 0.5101 | -0.06068 | [-0.10771, -0.01583] |
| Holdout, doubled costs | 86 | -7.7992 | 0.3701 | Negative | Not a separate sample |

All three ordinary segments lose, and every reported 95% expectancy interval is below zero. The
doubled-cost holdout becomes materially worse. Parameter tuning on the same ledger would create a
high overfitting risk, not reliable evidence of improvement.

### 6. The simulator omits several costs that would not rescue the result

The model records observed spread and modeled fees/slippage, but decision latency, submit latency,
fill latency, adverse selection, queue position, and funding are not modeled. These omissions make a
live-readiness claim impossible. They are more likely to worsen execution than to turn a clearly
negative PAPER strategy positive.

### 7. No tested replacement is ready

The XAU KAMA raw shadow is also negative and quarantined. At the cutoff it had 520 modeled closes,
net PnL about -13.1696 USDT, and profit factor 0.0135 under the baseline execution profile. The
filtered XAU family had only one close, which is insufficient evidence. Entry V3 remains a separate
non-executing capture timeline. None is eligible to replace the failed crypto baseline.

## Improvement Plan

### P0 - Implemented now

1. Keep the current run PAPER-only, flat, and blocked from new entries.
2. Add a durable evidence gate that evaluates only normal closes.
3. Require at least 100 normal trades over at least 10 UTC trading days.
4. Split the sample chronologically and halt when both halves are negative with profit factor below
   0.80.
5. Persist `STRATEGY_EVIDENCE_FAILED`, block entries before candidate selection, and refuse ordinary
   Resume.
6. Do not force-close a position solely for strategy evidence; preserve normal stop, take-profit, and
   time-exit behavior. Daily-loss and drawdown limits retain higher priority and may flatten.

### P1 - Required before another executable strategy

1. Freeze code, config, data hashes, cost assumptions, and run identity for each experiment.
2. Build challengers offline from causal features only. Start with fewer, clearer hypotheses rather
   than adding indicators to the current strategy.
3. Measure MFE, MAE, post-exit returns, symbol, direction, regime, spread, and time-of-day to determine
   whether any economic edge exists before optimizing parameters.
4. Reject strategies whose gross price PnL is non-positive. Fee reduction cannot repair them.
5. Prefer lower-turnover candidates unless higher frequency survives doubled costs.

### P2 - Promotion evidence

1. Run chronological replay with purge or embargo where feature overlap requires it.
2. Preserve an untouched holdout and run rolling walk-forward evaluation.
3. Stress fees, spread, slippage, latency, missed fills, and funding.
4. Require positive net expectancy, profit factor above 1, controlled drawdown, and stability across
   more than one regime. A confidence interval crossing zero is not enough.
5. Start a fresh PAPER run only after manual approval. No automatic promotion is permitted.

### P3 - Do not do

- Do not raise leverage, notional, or trade frequency.
- Do not loosen the evidence gate to make Resume succeed.
- Do not count recovery profit as alpha.
- Do not add martingale, DCA, or a forced daily-profit target.
- Do not spend more time polishing the dashboard before a challenger has economic evidence.

## Implemented Remediation

The implementation adds `[paper_strategy.evidence_halt]` with conservative defaults, configuration
validation, chronological-half diagnostics, a pre-entry fail-closed check, persisted halt reason,
specific operator messaging, tests, and specification updates. The diagnostic state is one of
`DISABLED`, `COLLECTING`, `PASS`, or `FAILED`.

`PASS` means only that the retirement condition did not trigger. It must never be presented as proof
of profitability. `FAILED` is sticky across restart and is intentionally not cleared by ordinary
Resume. The evidence gate ignores manual and outage-held closes.

## Post-Cutoff Incident and Deployment Verification

At 2026-09-23 03:06:51 UTC, before the new code was deployed, the dashboard log recorded a successful
loopback `POST /api/control/resume`. The available log identifies only the local host and does not
attribute the requester. The old process then opened a BCH_USDT PAPER short at 03:06:56 UTC and closed
it at `STOP_LOSS` at 03:08:23 UTC for -0.26506956 USDT net. This trade occurred after the fixed audit
cutoff and is not included in the 426-trade tables above.

After deployment, the ledger contained 427 normal closes over 11 UTC trading days. The evidence gate
reported `FAILED`: the first half was -11.40415498 USDT at 0.5329 profit factor and the second half was
-13.44006356 USDT at 0.4811 profit factor. Three later live-data polling cycles all reported
`HALTED`, `armed=false`, zero positions, `STRATEGY_EVIDENCE_FAILED`, `resume_allowed=false`, valid
accounting, and live Gate public data. Current equity after the post-cutoff trade was 279.91343380
USDT. This verifies that an ordinary Resume can no longer re-arm the failed strategy.

## Limitations and Decision Boundary

- This is a PAPER audit. It does not establish live performance or authorize real orders.
- The fixed ledger is observational. It cannot prove which untested alternative strategy would win.
- The runtime-reported run commit predates the source checkout at audit time, so the run is not a
  fully immutable code-to-ledger experiment.
- Bootstrap intervals resample observed trade outcomes; they do not reproduce market impact or a
  causal counterfactual execution path.
- The current evidence justifies stopping the failed strategy. It does not justify promoting XAU,
  Entry V3, or any other challenger.

## Final Decision

Keep the current strategy halted. The next valuable work is an offline, immutable challenger study,
not another live parameter adjustment. Resume only through a fresh, explicitly approved PAPER
experiment after the challenger passes chronological, cost-stressed, holdout, and walk-forward gates.
