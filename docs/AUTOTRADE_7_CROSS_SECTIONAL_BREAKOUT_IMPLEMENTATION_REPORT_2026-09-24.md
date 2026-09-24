# AUTOTRADE 7.0 Cross-Sectional Breakout Implementation Report

Report date: 2026-09-24

Evidence cutoff: 2026-09-24T05:36:38Z for the final runtime/browser check and 2026-09-24T04:16:23Z for the research report

Scope: PAPER-only research, validation, reporting, and dashboard integration

Decision: no strategy is eligible for PAPER execution and no execution authority was changed

## 1. Executive Summary

AUTOTRADE 7.0 now has a separate wide-crypto research family named `WIDE_CRYPTO_RESEARCH_V1`. It freezes a dynamic Gate USDT perpetual universe, computes causal cross-sectional momentum and prior-only breakout features, runs three fundamentally different strategy variants on one timeline, applies chronological and combinatorial validation, records append-only evidence, and exposes the result in the dashboard.

The implementation works, but the strategies do not. The correct decision is to keep trading halted.

- `CS_MOMENTUM_ONLY_V1` was the least bad research lead. It produced +23.2581 bps gross expectancy and +4.6393 bps modeled net expectancy over the complete sample, but profit factor was only 1.0147. Test expectancy was -28.7934 bps, final holdout expectancy was -111.7509 bps, and stressed expectancy was -7.6831 bps.
- `CTA_BREAKOUT_ONLY_V1` was negative before costs: -24.5316 bps gross expectancy.
- `CROSS_SECTIONAL_BREAKOUT_V1` was also negative before costs: -53.6559 bps gross expectancy. Its four pre-registered edge/cost neighbors produced identical negative pre-holdout results, so parameter stability was not proven.
- PAPER-eligible strategies: 0.
- Activated strategies: 0.
- LIVE remains unavailable.

The existing production baseline explains the continuing losses more directly. Its normal ledger contains 427 closes, 33.02% wins, -12.1793 USDT gross price PnL, 12.6649 USDT fees, and -24.8442 USDT net PnL. Gross price movement was already negative. Fees approximately doubled the loss, but removing fees would not make the baseline profitable. The strategy's realized edge, selectivity, and calibration are the primary failures.

## 2. Repository Baseline

The pre-existing system remains a PAPER simulator with the dashboard separated from the trading engine. At the cutoff:

- Mode: `PAPER`
- Venue: `GATE`
- Trading state: `HALTED`
- Engine: `SIMULATION_READY`
- Strategy armed: `false`
- Open trade: none
- Accounting: `VALID`
- Market data: `LIVE`
- Risk: `STRATEGY_EVIDENCE_FAILED`
- Research: `READY`
- Resume allowed: `false`
- Auto-resume allowed: `false`
- Market scope: `WIDE_CRYPTO`

The last normal baseline evidence is economically negative:

| Metric | Result |
|---|---:|
| Normal closes | 427 |
| Wins / losses | 141 / 286 |
| Win rate | 33.0211% |
| Break-even win rate | 49.3397% |
| Gross price PnL | -12.17927339 USDT |
| Fees | -12.66494518 USDT |
| Net PnL | -24.84421854 USDT |
| Profit factor | 0.5062 |
| Turnover | 25,329.8894 USDT |
| Maximum drawdown | 24.9691 USDT |

Recovery-flatten or outage-held results are excluded from strategy expectancy. They are operational recovery evidence, not alpha.

## 3. Why the Existing Strategy Failed

The loss is not a single bug. It is an economically weak decision process amplified by friction.

1. **Negative raw edge.** Gross price PnL was -12.1793 USDT before fees. This alone disproves the idea that fees are the only problem.
2. **Win rate below the payoff requirement.** Winners and losers were nearly symmetric in size, while the 33.02% win rate was far below the 49.34% break-even rate.
3. **High turnover relative to edge.** The system generated 427 normal closes and 25,329.89 USDT of turnover. Repeated marginal entries converted small forecasting errors into persistent fees.
4. **Expected edge was miscalibrated.** Runtime estimates can show positive expected net bps while the realized ledger remains negative. Those estimates are ranking heuristics, not calibrated probabilities or executable return forecasts.
5. **Weak selectivity.** The old family traded too many conditions that did not survive real price movement, fees, and short holding periods.
6. **No robust out-of-sample proof.** Earlier families either failed economically or lacked independent samples. None met the promotion policy.

The 80/20 conclusion is simple: stop optimizing the dashboard or resume control. The bottleneck is a missing robust edge after costs.

## 4. External Research Reviewed

The implementation reviewed ideas and failure modes from the following primary repositories and official documentation. No reported return was copied or treated as evidence for AUTOTRADE.

- [Epsilon Quant Research](https://github.com/Epsilon-Fund/Epsilon-Quant-Research) and its [momentum CPCV design](https://github.com/Epsilon-Fund/Epsilon-Quant-Research/blob/main/topics/momentum/strategies/momentum_cpcv/README.md): contiguous group validation, purging, embargo, execution lag, and cost-aware evaluation.
- [Matias Jarnal crypto momentum](https://github.com/matiasjarnal/crypto-momentum-strategy): cross-sectional ranking, multiple momentum horizons, BTC regime context, and liquidity screens.
- [Yukai strategy portfolio](https://github.com/yukai1625/freqtrade-strategy-portfolio): Donchian-style CTA breakout, ATR exits, volatility, and volume filters.
- [NautilusTrader](https://github.com/nautechsystems/nautilus_trader): existing event-driven runtime capabilities remain the preferred execution architecture. This task did not replace that runtime.
- [Hummingbot funding-rate arbitrage](https://github.com/hummingbot/hummingbot/tree/master/controllers/generic/funding_rate_arb): useful two-leg lifecycle concepts, but insufficient to justify a funding strategy without fill, hedge, and settlement evidence.
- [Nivalume](https://github.com/nivalume/nivalume): bounded autonomous experiment-loop concepts. AUTOTRADE uses a fixed experiment budget rather than indefinite self-optimization.
- [Gate API v4 documentation](https://www.gate.com/docs/developers/apiv4/en/): public contract, ticker, and candlestick endpoints used to freeze the dataset without credentials.

What was borrowed: research structure and testable concepts. What was not borrowed: return claims, fitted parameters, exchange assumptions, or promotion decisions.

## 5. Architecture

The research path is isolated from execution:

```text
Gate public API
  -> snapshot universe and contract metadata
  -> immutable 15m, 1h, and 4h dataset with hashes
  -> causal feature timeline
  -> three fixed strategy families on one timeline
  -> costs, lag, exits, and one-position replay
  -> 50/20/15/15 chronology + CPCV + stress + null diagnostics
  -> append-only registry and lifecycle records
  -> reports/research-latest.json
  -> read-only dashboard panels
```

The dashboard does not place trades. The research runner does not call Resume, change leverage, arm a strategy, place an order, auto-promote a candidate, or enable LIVE trading.

## 6. Dataset

The canonical dataset used for all reported wide-crypto results is:

| Field | Value |
|---|---|
| Family | `WIDE_CRYPTO_RESEARCH_V1` |
| Dataset ID | `wide-crypto-v1-713c2164956dc890d326116e` |
| Identity SHA-256 | `565cf7a8b3aa8c98f843020493add3eeddbff30fb7da7bda5e68faf836066176` |
| Source | Gate public API v4 |
| Start | 2026-06-26T03:00:00Z |
| End | 2026-09-24T03:00:00Z |
| Duration | 90 days |
| 15-minute rows | 172,820 |
| 1-hour rows | 43,220 |
| 4-hour rows | 10,800 |
| Code commit at freeze | `588f2c4521fddbb0667a92784e68000786363253` |
| Config SHA-256 | `55a7e153b3d4c76291eb21c91efb04ba874bb02b104ad0155f737299a95cb9a8` |
| Status | `FROZEN`, immutable, content-hashed |

File identities:

| File | SHA-256 |
|---|---|
| `candles_15m.jsonl` | `5fa6890f1f796fdbb2a4ceeeb1c4dd79c1d62339446ecde6814f207fe8f6e7c9` |
| `candles_1h.jsonl` | `13353b202369dd64b7b7dd32af8b8e60be2dd2dca1decaf75a8025c21f850801` |
| `candles_4h.jsonl` | `95dd70f0b76376bbf480bfaa65164fbe301564ce1ee696a2ae00c4e696eea561` |
| `universe.json` | `befbfcea856bee2d94d96690241e74b24fe200e804c70ed7ccbc65c7ac3412fa` |

The CLI can verify these hashes and can replay a supplied frozen dataset instead of silently refetching a new universe.

## 7. Universe Construction

The final universe contains 20 Gate USDT perpetual contracts:

`BTC`, `ETH`, `SOL`, `ZEC`, `SNDK`, `XRP`, `NEAR`, `DOGE`, `BCH`, `UNI`, `SUI`, `HYPE`, `ADA`, `TRUMP`, `PEPE`, `TAKE`, `BNB`, `AKE`, `MU`, and `TAO`.

The selector filters contract status, USDT settlement, non-crypto references, stablecoin bases, listing age, snapshot quote liquidity, spread, and history coverage, then applies a 15-to-30 instrument cap. Contract tick size, lot size, minimum order size, snapshot bid/ask, mark, index, funding, listing age, quote volume, and rejection reasons are retained.

Critical limitation: selection uses the current contract snapshot. Delisted instruments and historical changes in liquidity are unavailable, so survivorship bias remains. Snapshot spreads and funding are metadata, not historical series.

## 8. Momentum Model

`CS_MOMENTUM_ONLY_V1` ranks the universe cross-sectionally using 12-hour, 24-hour, and 72-hour prior returns. Each horizon is standardized across the available universe, combined with fixed weights, and converted to average ranks so ties are deterministic.

Signals are computed only from data available at the decision timestamp. Entry occurs at the next 1-hour bar open. Long and short tails are symmetric, subject to liquidity, volatility, BTC regime, expected movement, and one-position limits.

The model is deliberately not machine learning. Ninety days of current-snapshot data is too small and biased to justify feature search or model fitting.

## 9. Breakout Model

`CTA_BREAKOUT_ONLY_V1` uses prior-only Donchian boundaries. The current bar is never included in its own breakout threshold. It also uses prior ATR, relative volume, volatility expansion, and maximum-extension checks.

The combined family requires both cross-sectional momentum direction and breakout confirmation. This prevents a breakout observation from being counted as confirmation if momentum disagrees.

The standalone CTA family failed at the most basic economic gate: gross expectancy was negative before modeled costs.

## 10. Regime Model

BTC 4-hour candles define a fixed context using fast and slow exponential averages plus the slow-average slope. The resulting states are `BULL`, `BEAR`, or `NEUTRAL`.

The regime is a filter, not a return forecast. Long candidates require compatible BTC conditions, short candidates require compatible opposite conditions, and ambiguous conditions can produce no trade. The model uses only completed 4-hour information available at the 1-hour decision timestamp.

## 11. Execution-Edge Model

Every trade includes:

- one-bar entry lag;
- maker/taker-style fee assumption from the configured execution profile;
- snapshot spread proxy;
- slippage;
- latency;
- adverse selection;
- impact allowance;
- funding allowance;
- an expected-movement-to-cost threshold.

The base edge/cost ratio is 3.0, with pre-registered diagnostic neighbors 2.5, 3.0, 3.5, and 4.0. The combined family's neighbors all produced the same 41 pre-holdout trades and -60.0285 bps net expectancy. This means the current movement proxy did not discriminate candidates in that range. Raising or lowering the threshold inside this neighborhood is not evidence of robustness.

Historical executable bid/ask and historical funding were unavailable. Therefore, even the stressed profile remains a model, not fill proof.

## 12. Exit Architecture

The replay allows at most one position. Fixed exits are:

- initial stop: 1.3 ATR;
- profit target: 2.7 ATR;
- trailing activation: 1.5R;
- trailing distance: 1.5 ATR;
- time stop: 18 hours;
- conservative intrabar resolution when more than one barrier could have been touched.

The engine records gross and net bps, holding time, exit reason, MFE, MAE, and a signed MFE-capture diagnostic. Negative signed capture means the trade lost despite having favorable excursion; it must not be read as a bounded percentage.

## 13. Strategy Variants

Three executable-in-replay but non-executing-in-runtime families were tested:

1. `CS_MOMENTUM_ONLY_V1`: cross-sectional ranking without Donchian confirmation.
2. `CTA_BREAKOUT_ONLY_V1`: prior Donchian breakout without cross-sectional ranking.
3. `CROSS_SECTIONAL_BREAKOUT_V1`: momentum rank plus breakout confirmation.

A funding-carry family exists only as a scaffold. It is disabled because two-leg lifecycle management, hedge reconciliation, partial-fill recovery, and funding settlement verification are not implemented and validated.

## 14. Same-Timeline Comparison

Only the three new families are directly comparable. They use the same dataset, symbols, timestamps, fee model, execution lag, cost model, and chronological boundaries.

All bps PnL and drawdown values below are sums of reference-notional trade returns. They are not account-equity returns and must not be compared with the baseline's USDT ledger.

| Strategy | Trades | Gross exp. bps | Net exp. bps | PF | Net PnL bps | Max DD bps | Test bps | Holdout bps | Stress bps |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `CS_MOMENTUM_ONLY_V1` | 231 | +23.2581 | +4.6393 | 1.0147 | +1,071.68 | 12,550.11 | -28.7934 | -111.7509 | -7.6831 |
| `CTA_BREAKOUT_ONLY_V1` | 176 | -24.5316 | -42.7960 | 0.6647 | -7,532.10 | 8,138.13 | -58.0869 | -23.9922 | -68.7415 |
| `CROSS_SECTIONAL_BREAKOUT_V1` | 54 | -53.6559 | -72.0153 | 0.6142 | -3,888.83 | 5,739.53 | -171.1638 | -109.8198 | -87.1950 |

`CS_MOMENTUM_ONLY_V1` is the best research lead only in the relative sense that it lost less. It is not a viable strategy and is not the current best strategy for execution. The correct execution answer is: none.

Existing XAU and Entry V3 experiments appear in the dashboard tournament for completeness, but their timelines or units are not comparable with this wide-crypto replay. The old REST Momentum result remains `NOT_COMPARABLE`; Entry V3 has no reconstructable accepted-trade series on this timeline; XAU variants have insufficient or negative evidence.

## 15. CPCV

Fixed-parameter combinatorial purged cross-validation uses 8 contiguous groups, 2 test groups per split, 28 combinations, a 24-hour purge, and a 24-hour embargo. No parameters are optimized inside folds.

| Strategy | Paths | Median OOS return bps | Median OOS exp. bps | Median PF | Positive paths | Return dispersion bps |
|---|---:|---:|---:|---:|---:|---:|
| `CS_MOMENTUM_ONLY_V1` | 28 | +491.61 | +11.38 | 1.0341 | 53.57% | 5,337.28 |
| `CTA_BREAKOUT_ONLY_V1` | 28 | -909.36 | -32.17 | 0.7211 | 0.00% | 707.57 |
| `CROSS_SECTIONAL_BREAKOUT_V1` | 28 | -634.46 | -105.74 | 0.4851 | 25.00% | 886.34 |

The paths overlap and are not independent. This implementation evaluates 28 fixed-parameter split results; it does not claim the 105 stitched backtest paths sometimes constructed in full CPCV workflows. CPCV does not rescue the strategies.

## 16. Walk-Forward

The primary chronology is a single sealed 50/20/15/15 progression:

| Stage | End |
|---|---|
| Selection, 50% | 2026-08-10T02:00:00Z |
| Validation, 20% | 2026-08-28T02:00:00Z |
| Test, 15% | 2026-09-10T14:00:00Z |
| Final holdout, 15% | 2026-09-24T03:00:00Z |

Stage net expectancy in bps:

| Strategy | Selection | Validation | Test | Final holdout |
|---|---:|---:|---:|---:|
| `CS_MOMENTUM_ONLY_V1` | +15.1568 | +118.1008 | -28.7934 | -111.7509 |
| `CTA_BREAKOUT_ONLY_V1` | -60.2566 | -18.4650 | -58.0869 | -23.9922 |
| `CROSS_SECTIONAL_BREAKOUT_V1` | -64.5328 | +46.4340 | -171.1638 | -109.8198 |

The sharp reversal from validation to test and holdout is exactly why promotion cannot use selection or validation alone. Ninety days also does not cover enough independent market regimes for a general profitability claim.

## 17. Final Holdout

The final 15% was sealed after dataset, code, strategy identities, and parameters were fixed. Holdout access count is 1.

| Strategy | Holdout trades | Net exp. bps | Profit factor | Result |
|---|---:|---:|---:|---|
| `CS_MOMENTUM_ONLY_V1` | 44 | -111.7509 | 0.7606 | Fail |
| `CTA_BREAKOUT_ONLY_V1` | 32 | -23.9922 | 0.7848 | Fail |
| `CROSS_SECTIONAL_BREAKOUT_V1` | 13 | -109.8198 | 0.5061 | Fail and insufficient sample |

The holdout result invalidates any promotion case based on complete-sample or validation optimism.

## 18. Execution Stress

The stressed profile increases modeled friction. Results:

| Strategy | Stressed net exp. bps | Stressed PF | Result |
|---|---:|---:|---|
| `CS_MOMENTUM_ONLY_V1` | -7.6831 | 0.9764 | Fail |
| `CTA_BREAKOUT_ONLY_V1` | -68.7415 | 0.4943 | Fail |
| `CROSS_SECTIONAL_BREAKOUT_V1` | -87.1950 | 0.5517 | Fail |

Stress results are necessary but not sufficient. The absence of historical BBO and funding series means they remain conservative simulations, not executable-fill validation.

## 19. Parameter Stability

The combined family was evaluated at edge/cost thresholds 2.5, 3.0, 3.5, and 4.0 before opening the final holdout. Every neighbor produced:

- 41 trades;
- -60.0285 bps net expectancy;
- -2,461.17 bps net PnL;
- 0.6576 profit factor.

This is not a stable profitable plateau. It is a flat negative plateau caused by a threshold that does not bind in the tested neighborhood. Parameter stability is `NOT_PROVEN`.

## 20. Overfitting Audit

The bounded experiment budget was respected:

- experiments run: 7;
- strategy families: 3;
- parameter variants: 4;
- final holdout accesses: 1.

Diagnostics:

| Diagnostic | Status | Reason |
|---|---|---|
| Deflated Sharpe Ratio | `UNAVAILABLE` | Sparse and non-IID trade frequency makes the statistic misleading |
| Probability of Backtest Overfitting | `UNAVAILABLE` | Only four pre-registered diagnostic neighbors, not a valid strategy-selection matrix |
| White's Reality Check | `UNAVAILABLE` | A defensible block length is not calibrated |
| Synthetic null | `AVAILABLE` | 33 daily observations, 1,000 deterministic sign-flip trials |

For the combined family, observed mean daily PnL was -117.8432 bps and the one-sided null p-value was 0.8561. There is no evidence that its performance exceeds a no-skill sign-flip null. Unavailable diagnostics are reported honestly instead of replaced with fabricated precision.

## 21. Counterfactual Analysis

The combined family produced 900 candidates:

- observed: 54;
- rejected and resolved: 844;
- avoided loss: 413;
- missed opportunity: 412;
- neutral: 21;
- average rejected modeled net outcome: +98.7365 bps.

The rejection model avoided and missed almost equal numbers of outcomes. More importantly, overlapping candidates are not independent trades. This counterfactual is useful for diagnosing filters, but it cannot be added to actual PnL or presented as foregone executable profit.

## 22. Strategy Tournament

Current decision table:

| Family | Evidence status | Decision |
|---|---|---|
| Existing REST Momentum | 427 normal closes, gross and net negative, PF 0.5062 | Keep halted / retired from execution consideration |
| XAU KAMA raw | Negative and quarantined | Reject |
| XAU KAMA filtered | One trade | Observe only |
| XAU fair value | One replay trade / reference-only runtime | Experimental only |
| XAU combined | No trades | Insufficient evidence |
| Entry V3 | Separate capture timeline, no accepted reconstructable trades | Experimental only |
| `CS_MOMENTUM_ONLY_V1` | Positive full-sample gross, but failed test, holdout, stress, and PF floor | Research lead only |
| `CTA_BREAKOUT_ONLY_V1` | Negative gross and net | Reject current definition |
| `CROSS_SECTIONAL_BREAKOUT_V1` | Negative gross and net, failed holdout and stress | Reject current definition |
| Funding carry scaffold | Execution disabled, lifecycle incomplete | Do not implement execution yet |

Winner for PAPER execution: none.

## 23. Promotion State

All new families remain `EXPERIMENTAL`. The promotion gate is fail-closed. Common blockers include:

- negative test or holdout expectancy;
- stressed execution failure;
- profit factor below 1.10;
- parameter stability not proven;
- execution model not validated;
- historical BBO unavailable;
- historical funding unavailable;
- current-snapshot survivorship bias;
- insufficient independent sample for the combined family.

The registry and lifecycle history are append-only. Promotion requires explicit operator approval even after every evidence gate passes. Automatic promotion is disabled.

## 24. Dashboard

The dashboard now reads the research artifact and exposes four new read-only areas:

- **Strategy Tournament:** comparable status, sample, expectancy, profit factor, drawdown, stage outcomes, stress, promotion state, and blockers.
- **Universe:** frozen dataset identity, symbols, coverage, selection timestamp, snapshot liquidity/spread context, and survivorship warning.
- **Top Candidates:** rank, symbol, direction, strategy, modeled movement, costs, edge/cost ratio, filters, and resolution state.
- **Overfitting:** experiment budget, DSR/PBO/White's Reality Check availability, null test, parameter neighborhood, and holdout access.

No research control was added to Resume, Pause, leverage, or order routing. Mobile layout, keyboard focus, contrast, overflow, loading, empty, and error states follow the existing operational-console design.

## 25. Tests

Acceptance gates used for this delivery:

- Python unit and integration suite through `check.bat`;
- Ruff lint;
- mypy type checking;
- Playwright dashboard suite through `check_ui.bat`;
- `pip check` dependency validation;
- frozen dataset hash verification;
- browser-visible dashboard review;
- repeated runtime polling after refresh;
- Git whitespace and status checks.

Final results:

- 144 Python tests passed in 12.5 seconds;
- Ruff passed;
- mypy passed across 38 source files;
- 5 Playwright tests passed in 22.7 seconds;
- `pip check` reported no broken requirements;
- the live dashboard exposed 9 tournament rows, 50 universe rows, 50 top-candidate rows, and the overfitting record `7 experiments / 3 families / 4 variants / 1 holdout access / 28 CPCV paths`;
- the live page remained `HALTED`, showed `NO STRATEGY ELIGIBLE`, and kept Resume disabled after a cache-bypassing hard reload.

## 26. Bugs Found

1. The first exploratory universe admitted `XAG_USDT`, a non-crypto reference contract, because the exclusion set covered stablecoins but not metal references.
2. `full` always fetched a new snapshot universe even when `--dataset` supplied a frozen dataset, weakening deterministic replay.
3. The new holdout seal initially used one global filename, so a later legitimate dataset could conflict with the first dataset's identity.
4. The edge/cost proxy did not discriminate any of the four tested combined-family thresholds.
5. Signed MFE capture can be negative and unbounded, so it cannot be presented as a percentage.
6. Historical BBO and funding data are not available in the frozen source, preventing executable-fill validation.

## 27. Bugs Fixed

1. Added explicit exclusion for non-crypto references including `XAG`, `XAU`, `XAUT`, and `PAXG`. The contaminated exploratory dataset was not used in final evidence.
2. Wired `full --dataset <frozen-path>` to verify and reuse the exact immutable dataset rather than refetching.
3. Scoped holdout seals by dataset under the ignored experiment evidence tree.
4. Preserved all missing-market inputs as explicit limitations rather than defaulting them to zero or claiming availability.
5. Kept strategy promotion fail-closed, so the weak results cannot affect PAPER execution.

The weak edge/cost discrimination and missing historical microstructure are research blockers, not bugs that can be safely patched with invented data.

## 28. Remaining Risks

**P0 - Must remain blocked**

- No strategy passed test, final holdout, and stressed execution together.
- Current universe construction has survivorship bias.
- Candle bars cannot prove fill quality or intrabar ordering.
- Historical funding and executable BBO are absent.

**P1 - Important before another promotion attempt**

- Acquire point-in-time universe membership and delisting history.
- Capture BBO/order-book and funding histories aligned to exchange timestamps.
- Extend the sample across multiple volatility and liquidity regimes.
- Calibrate expected movement against realized conditional outcomes rather than ATR alone.

**P2 - Later**

- Implement full stitched CPCV paths if a strategy first shows positive gross and stable chronological evidence.
- Consider bounded ML only after data quality, sample size, and execution labels are adequate.
- Build funding carry only after two-leg state recovery and settlement reconciliation are proven.

**P3 - Ignore for now**

- More dashboard polish.
- More parameters on the current negative strategy definitions.
- LIVE connectivity.
- Any request to force trades or force a daily profit target.

## 29. Changed Files

Core implementation:

- `autotrade/wide_crypto_data.py`
- `autotrade/wide_crypto_strategy.py`
- `autotrade/robust_validation.py`
- `autotrade/wide_crypto_cli.py`
- `config/wide_crypto_research.toml`
- `tests/test_wide_crypto_research.py`

Dashboard:

- `dashboard/index.html`
- `dashboard/app.js`
- `dashboard/styles.css`
- `tests/e2e/dashboard.spec.js`

Specifications and operating documentation:

- `README.md`
- `docs/ARCHITECTURE.md`
- `docs/BACKTEST_VALIDATION.md`
- `docs/DASHBOARD_UI.md`
- `docs/DATA_SPEC.md`
- `docs/RESEARCH_ENGINE.md`
- `docs/RISK_MANAGEMENT.md`
- `docs/SECURITY.md`
- `docs/STRATEGY_PROMOTION_POLICY.md`
- `docs/TRADING_SPEC.md`
- this report

Generated datasets, experiment ledgers, seals, and `reports/research-latest.json` are ignored evidence artifacts and are not committed.

## 30. Git Commits

- Baseline before this task: `e4e1c49` - existing research promotion implementation report.
- Core implementation: `588f2c4521fddbb0667a92784e68000786363253` - wide-crypto dataset, strategies, validation, dashboard, tests, and specifications.
- Final delivery commit: the commit containing this report and deterministic replay/holdout-seal corrections; exact SHA is recorded in the final task response.

The user-owned untracked file `docs/AUDIT_REPORT_2026-09-18_POST_RESTART.md` is intentionally excluded.

## 31. Current PAPER Runtime

At 2026-09-24T05:36:38Z:

| Control | State |
|---|---|
| Mode | `PAPER` |
| Trading state | `HALTED` |
| Strategy armed | `false` |
| Position | Flat |
| Accounting | `VALID` |
| Data | `LIVE` |
| Risk | `STRATEGY_EVIDENCE_FAILED` |
| Research | `READY` |
| Resume | Not allowed |
| Auto-resume | Not allowed |
| Execution authority changed | No |
| New strategy activated | No |
| LIVE orders | Unavailable |

Accounting validity is necessary but not sufficient to resume. The economic evidence gate is failed, so the halt is correct.

## 32. Recommended Next Experiment

Do not tune these three families further on this holdout. That would contaminate the final evidence and encourage overfitting.

The highest-value next experiment is a new, independently frozen dataset with point-in-time universe membership and synchronized BBO/funding capture. Re-test only `CS_MOMENTUM_ONLY_V1` as the research lead, with no parameter expansion, after enough new data accumulates to create an untouched forward window. Require:

1. positive gross expectancy in test and forward holdout;
2. positive net expectancy under stressed costs;
3. profit factor above 1.10 with a meaningful independent sample;
4. stable results across chronological windows and adjacent fixed parameters;
5. executable BBO and funding evidence;
6. a manual promotion decision.

If it fails again, retire this family rather than adding complexity. The opportunity cost of more tuning is higher than the value of improving data quality and execution evidence.
