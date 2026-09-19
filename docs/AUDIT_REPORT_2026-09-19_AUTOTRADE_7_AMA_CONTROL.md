# AUTOTRADE 7.0 XAU KAMA/AMA20 Control Audit

## Executive Summary

AUTOTRADE 7.0 now has an execution-isolated XAU KAMA control, but it does not yet have enough valid
same-timeline evidence to judge strategy quality. The control observes `XAU_USDT`, `XAUT_USDT`, and
`PAXG_USDT` continuously in both market scopes, computes KAMA 10/20/50, emits paired raw and filtered
decisions, records decision-time features and later counterfactuals, and publishes comparison reports.

The current PAPER strategy remains economically negative over the run. Recovery results remain
separate from normal strategy evidence. The current strategy, Entry V3, and KAMA control do not yet
share one complete comparable timeline, so no superiority claim is valid. Nothing in this change can
route an AMA decision to an order. Entry V3 remains execution-disabled.

Cutoff: `2026-09-19 06:38:41 UTC` (`2026-09-19 13:38:41 WIB`). Repository base: `d227aae`. Run:
`aa89e54a-e88a-4732-b254-615a58f1721a`.

## Baseline

At implementation start, the latest fixed-cutoff audit showed 315 normal closes, -18.38589949 USDT
normal net, 0.5004 profit factor, and -0.05836793 USDT/trade expectancy. Three recovery closes added
+4.68891114 USDT but are not normal strategy alpha. XAU had no closed trades.

At this audit cutoff, the run-ledger baseline has 340 normal closes, -9.30621005 USDT gross price PnL,
10.05427261 USDT fees, -19.36048265 USDT net, 0.50854836 profit factor, -0.05694260
USDT/trade expectancy, and 19.36048265 USDT maximum drawdown. This remains negative evidence, not a
release signal.

## Root Causes

1. The existing engine had no independent same-timeline control, so complexity had no defensible
   opportunity-cost benchmark.
2. Normal strategy economics were already negative after costs; more indicators or more trades were
   not the bottleneck.
3. Rejected XAU candidates did not have a dedicated later-outcome stream, preventing measurement of
   filter benefit versus missed opportunity.
4. The PAPER model does not model latency, queue position, fill probability, missed fills, market
   impact, or adverse selection.
5. XAU had no closed strategy sample, and no XAU holdout or walk-forward result existed.
6. The first pre-validation control draft did not explicitly enforce the required KAMA20 slope sign.
   That defect was found before shipment. Its V1 stream is preserved but excluded; V2 starts a clean
   hash chain with the corrected rule.

## Architecture Before

- `PaperTrader` owned PAPER execution, risk, accounting, checkpointing, and recovery.
- `MarketScopeController` durably selected `WIDE_CRYPTO` or `XAU_ONLY`.
- `XauSignalEngine` could create executable XAU PAPER candidates only in XAU scope.
- Entry V3 consumed an independent WebSocket capture stream with execution disabled.
- The dashboard was a local read/control plane, not a trading engine.
- No persistent independent KAMA control or control-specific counterfactual report existed.

## Architecture After

- `AmaControlEngine` receives the retained XAU/reference rows from the same five-second REST poll.
- It runs before scope-specific entry handling and in both scopes.
- It owns no Nautilus engine, candidate submitter, sizing authority, or order API.
- `execution_enabled=true` is rejected at configuration load and engine construction.
- V2 evidence is append-only and hash-chained under the current run.
- Restart verifies the chain and restores KAMA state, shadow positions, trades, and pending outcomes.
- The dashboard consumes a read-only snapshot and offers no AMA execution or promotion control.

## Files Modified

- `.gitignore`
- `README.md`
- `autotrade/ama_control.py`
- `autotrade/config.py`
- `autotrade/dashboard.py`
- `autotrade/paper.py`
- `config/paper.toml`
- `dashboard/app.js`
- `dashboard/index.html`
- `dashboard/styles.css`
- `docs/ARCHITECTURE.md`
- `docs/BACKTEST_VALIDATION.md`
- `docs/DASHBOARD_UI.md`
- `docs/DATA_SPEC.md`
- `docs/AUTOTRADE_7_NEXT_CYCLE_BASELINE.md`
- `tests/e2e/dashboard.spec.js`
- `tests/test_ama_control.py`
- `tests/test_config.py`

Runtime derivatives are written under `reports/` and `data/runs/<run_id>/`; both are intentionally
Git-ignored. The pre-existing untracked `docs/AUDIT_REPORT_2026-09-18_POST_RESTART.md` was preserved
and not included in this change.

## Migrations

No ledger, checkpoint, position, or market-scope migration was performed. V2 uses
`ama-control-v2.jsonl`; the pre-validation V1 file is retained and never imported into V2 metrics.
Report files are regenerated atomically from the active V2 state.

## AMA Implementation

The control uses Kaufman's Adaptive Moving Average, not an SMA substitution. Efficiency ratio and
fast/slow smoothing are calculated incrementally for configured 10/20/50 periods. Parameters are
validated at startup and hashed into a parameter-set ID.

The quote-step volatility field is explicitly an ATR proxy, not candle true range. Gate receive time
is the observation clock because the ticker row lacks a reliable exchange event timestamp for this
purpose.

## AMA10/20/50 Monitor

The monitor classifies `STRONG_UPTREND`, `UPTREND`, `STRONG_DOWNTREND`, `DOWNTREND`, `CHOP`,
`TRANSITION`, and `UNKNOWN`. It requires all configured periods before leaving warm-up and tracks
regime persistence before the filtered control may accept a candidate.

## AMA Raw Control

The raw V2 candidate is LONG only when price is above KAMA20 and KAMA20 slope is positive, SHORT only
when price is below KAMA20 and slope is negative, otherwise WAIT. Raw shadows use the same notional
and execution profiles as filtered shadows. Raw decisions never reach the PAPER order path.

## AMA Filtered Control

The filtered family applies only the centralized quality gate: readiness, stale state, risk validity,
spread, liquidity, reference state, regime alignment/persistence, minimum slope, hysteresis, maximum
extension, net edge, confidence, and quarantine. The open shadow is invalidated symmetrically after an
opposite KAMA20 hysteresis-band crossing or at the configured maximum hold.

## Hysteresis

The symmetric band is the maximum of configured minimum hysteresis, volatility proxy multiplied by
the ATR factor, and modeled baseline cost. Current defaults are 3 bps minimum, 1.5 volatility factor,
and 17 bps zero-spread baseline cost before observed spread. Maximum entry extension is 50 bps.

## Regime Logic

KAMA ordering and slopes identify directional regimes. Small fast/slow separation with near-flat
KAMA20 is CHOP; non-aligned states are TRANSITION. This remains a compact monitoring taxonomy, not a
separate executable strategy.

## Entry Quality Gate

`EntryQualityGate` returns one deterministic decision code. Candidate counts, allows, rejects, and
rejection reasons are persisted and surfaced. Confidence is labeled `HEURISTIC_NOT_CALIBRATED`.
Missing or abnormal reference data cannot create permission; reference requirements are configurable.

## Counterfactual Logger

Every persisted candidate can receive 10, 30, 60, 180, 300, and 900 second outcomes. Each result
retains decision identity, decision-time cost including observed spread, gross and modeled net return,
MFE, MAE, and avoided-loss/missed-opportunity classification. Outcomes use only observations received
after the decision. They are modeled counterfactuals, not executable fills.

## Attribution

Decision records contain run/session, strategy/version/feature/execution/parameter identity, UTC time,
symbol/scope/direction, KAMA values and slopes, distance, volatility proxy, regime, reference state,
expected gross/cost/net edge, threshold, confidence, rejection reason, notional, leverage, execution
assumptions, and an initially empty outcome. The append-only record is immutable after hashing.

## XAU Reference Engine

The reference engine uses the median of available XAUT/PAXG prices, measures reference dispersion and
XAU dislocation, and labels `HEALTHY`, `DEGRADED`, `ABNORMAL`, or `UNAVAILABLE`. It is research-only;
`XAU_USDT` remains the primary market and no fair-value strategy is implemented.

## Strategy Health

Raw and filtered controls report sample, wins/losses, win rate, gross, costs, net, expectancy, profit
factor, maximum drawdown, MFE/MAE, side, regime, open position, and execution-profile sensitivity.
The current main strategy is shown only as `RUN_LEDGER_NOT_SAME_TIMELINE` and `NOT_COMPARABLE`.

## Quarantine

Shadow quarantine requires the configured minimum sample and both low profit factor and negative
recent net. It affects only the filtered shadow family, preserves logging, does not reactivate itself
from a single result, and has no effect on current PAPER entry routing.

## Execution Realism

| Profile | Included | Excluded |
|---|---|---|
| IDEALIZED | round-trip fee | spread, slippage buffer, funding buffer, latency/fill mechanics |
| BASELINE | fee, observed spread, fixed slippage buffer, funding buffer | latency, queue, partial/missed fills, impact, adverse selection |
| STRESSED | baseline cost multiplied by 1.5 | same unmodeled mechanics |

Any apparent edge that exists only in IDEALIZED is not acceptable. No current result is executable-fill
proof.

## Walk-Forward

No V2 chronological TRAIN → VALIDATION → TEST → FINAL HOLDOUT result exists. Random splitting is not
permitted. Parameter selection remains fixed a priori, and optimization status is `NOT_RUN`.

## Complexity Audit

**IS THE COMPLEX AUTOTRADE STRATEGY DEMONSTRABLY ADDING VALUE OVER AMA20?**

**INSUFFICIENT EVIDENCE**

The main strategy has a larger run-ledger sample but it is not a same-timeline XAU comparator. The V2
filtered family is below minimum sample, and no equivalent holdout, walk-forward, or stressed-fill
comparison exists. Raw PnL alone cannot answer the question.

## Dashboard Changes

The permanent `XAU KAMA/AMA20 Control` panel shows price, KAMA 10/20/50, regime, raw/filtered signal,
hysteresis, quality result, reference health, evidence integrity, sample size, and the six-family
comparison. It states no execution influence and no automatic promotion. Desktop and mobile layouts
were Playwright-tested; the comparison table scrolls within its panel without page overflow.

## Mandatory Final Comparison

| Strategy | Sample | PF | Expectancy/trade | Net PnL | Max DD | Evidence status |
|---|---:|---:|---:|---:|---:|---|
| Current strategy | 340 | 0.5085 | -0.05694260 USDT | -19.36048265 USDT | 19.36048265 USDT | Run ledger; not same XAU timeline |
| AMA20 raw V2 | 0 | N/A | N/A | N/A | N/A | INSUFFICIENT EVIDENCE; one open shadow |
| AMA20 filtered V2 | 0 | N/A | N/A | N/A | N/A | INSUFFICIENT EVIDENCE |
| XAU fair value | N/A | N/A | N/A | N/A | N/A | Reference only; strategy not implemented |
| Combined | N/A | N/A | N/A | N/A | N/A | Not implemented |
| Entry V3 shadow | N/A | N/A | N/A | N/A | N/A | Separate capture timeline; execution disabled |

## No-Trade Audit

At cutoff the account was `ACTIVE`, armed=`true`, scope=`WIDE_CRYPTO`, with zero open PAPER positions.
The KAMA control was `COLLECTING`; current raw/filtered/action were `SHORT` / `WAIT` / `WAIT`, and the
quality result was `REGIME_MISMATCH`. A WAIT or rejection is a valid result. No trade-count or
daily-profit target exists.

For the 2026-09-19 UTC day through cutoff, normal PAPER closes were grouped as: 12 stop losses totaling
-2.39997927 USDT, seven take profits totaling +1.54455281 USDT, and six time exits totaling
-0.11915670 USDT. One manual flatten lost 0.03 USDT and remains outside normal strategy evidence.

## Tests

- `check.bat`: 122 unit/integration tests passed; Ruff passed; mypy passed for 30 source files.
- `check_ui.bat`: five Playwright tests passed.
- New failure-path coverage includes evidence tamper detection, restart reconstruction, execution
  enablement rejection, abnormal reference state, rejection taxonomy, slope/extension filters,
  counterfactual MFE/MAE and stored cost, and required Markdown reports.
- Browser console warnings/errors: none during live inspection.
- Page overflow: none; the wide comparison table uses an internal scroll container.

## Runtime Validation

- Restart used `stop_bot.bat`, durable flat checkpointing, hidden `start_dashboard.bat`, and
  `health_check.bat --once`.
- Startup was HALTED and unarmed, with auto-resume allowed only after live data and accounting checks.
- Health check restored ACTIVE PAPER; later polls remained armed with data LIVE and accounting VALID.
- Run ID remained unchanged; session ID changed as expected.
- Active scope remained `WIDE_CRYPTO`; no pending scope switch existed.
- V2 append-only evidence survived the second restart with integrity `VALID`.
- Controlled observation reached 60 V2 observations, 12 raw candidate records, zero filtered allows,
  12 filtered rejects, 76 resolved horizons, one open raw shadow, and no filtered closes. The sample is
  functional evidence only.
- AMA execution was false with execution influence `NONE`; Entry V3 execution remained false.
- Final checkpoint/event: `25134` / `4218`; positions: `0`.
- `git diff --check` and `git fsck --no-dangling` passed.

## Automated Reports

The runtime generated all requested latest surfaces:

- `reports/strategy_health_latest.md`
- `reports/ama20_control_latest.md`
- `reports/complexity_audit_latest.md`
- `reports/xau_research_latest.md`
- `reports/entry_quality_latest.md`
- `reports/counterfactual_latest.md`
- `reports/execution_realism_latest.md`
- `reports/walk_forward_latest.md`

Structured JSON comparison, parameters, execution realism, health, and reference-impact outputs are
also generated atomically.

## Known Limitations

- V2 observation time is local receive time, not exchange event time.
- Volatility is a quote-step movement proxy, not true candle ATR.
- The current strategy is not yet evaluated on the exact V2 XAU timeline.
- Fair-value and combined strategies are placeholders only.
- Entry V3 is a separate capture timeline.
- Reference weighting, per-feed timestamps, robust outlier history, and FX normalization are limited.
- Session breakdown and formal statistical confidence intervals are not implemented for V2.
- Latency, queue, partial/missed fills, impact, and adverse selection are unmodeled.
- Parameter sensitivity, purge/embargo, walk-forward, and final holdout have not run.
- A short live observation validates plumbing, not profitability.

## Remaining Risks

**P0 — Evidence comparability:** without a replay or forward harness feeding the main strategy and
controls the same immutable timeline and execution model, complexity attribution remains invalid.

**P1 — Execution realism:** a strategy can appear viable under fixed costs and fail after latency,
missed fills, adverse selection, or market impact.

**P1 — Filter starvation:** zero or few filtered trades may mean valid selectivity or a gate that is too
strict. Do not relax it until counterfactual outcomes and regime samples identify the responsible gate.

**P2 — Reference robustness:** improve per-feed event freshness and historical outlier treatment before
using the reference state in any future executable proposal.

## Next Recommended Experiment

P0: run a chronological cost-aware replay that sends one immutable XAU timeline through current main,
AMA raw, and AMA filtered with equivalent execution profiles. Freeze parameters, preserve a final
holdout, and report by regime. Continue extended PAPER collection meanwhile. Do not promote, loosen
risk, or enable live orders.

RESEARCH INFRASTRUCTURE INCOMPLETE
